import http.client
import json
import os
import pwd
import socket
import ssl
import subprocess
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from family_controls.adapters.http import (
    HTTPClient,
    RemoteError,
    TCPServer,
    UnixServer,
    unix_request,
)
from family_controls.domain import InvalidCommand, Unavailable


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return cert, key


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def endpoint(setup, certificate):
    cert, key = certificate
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    with serving(TCPServer(("127.0.0.1", 0), setup.service, "test-token", tls)) as server:
        yield SimpleNamespace(
            url=f"https://localhost:{server.server_port}",
            port=server.server_port,
            client=HTTPClient(str(cert)),
            cert=cert,
            server=server,
        )


HEADERS = {
    "Authorization": "Bearer test-token",
    "Idempotency-Key": "test-operation",
    "X-Family-Actor": "test-parent",
}


def test_tls_command_and_retry_end_to_end(endpoint):
    url = endpoint.url + "/v1/users/child/add-time"
    first = endpoint.client.request(url, method="POST", payload={"minutes": 25}, headers=HEADERS)
    second = endpoint.client.request(url, method="POST", payload={"minutes": 25}, headers=HEADERS)
    assert first["remainingPlayTimeSeconds"] == second["remainingPlayTimeSeconds"] == 5100
    assert second["duplicate"]
    history = endpoint.client.request(endpoint.url + "/v1/users/child/history", headers=HEADERS)
    assert history["events"][0]["details"]["actor"] == "test-parent"


def test_unknown_ca_and_hostname_are_rejected(endpoint):
    with pytest.raises(Unavailable):
        HTTPClient().request(endpoint.url + "/v1/users/child/status", headers=HEADERS)
    with pytest.raises(Unavailable):
        endpoint.client.request(
            f"https://127.0.0.1:{endpoint.port}/v1/users/child/status", headers=HEADERS
        )


@pytest.mark.parametrize(
    "url", ["http://localhost", "file:///etc/passwd", "https://user:pass@localhost"]
)
def test_unsupported_transports_are_rejected(url):
    with pytest.raises(InvalidCommand):
        HTTPClient().request(url)


def test_unauthorized_cannot_mutate(endpoint, setup):
    with pytest.raises(RemoteError) as error:
        endpoint.client.request(
            endpoint.url + "/v1/users/child/add-time", method="POST", payload={"minutes": 25}
        )
    assert error.value.status == 401
    assert setup.service.status("child")["revision"] == 0


@pytest.mark.parametrize(
    "path,method,body,expected",
    [
        ("/v1/users/child/add-time", "POST", b"{", 400),
        ("/v1/users/child/add-time", "POST", b"[]", 400),
        ("/v1/users/child", "POST", b"{}", 404),
        ("/v1/users/child/add-time/extra", "POST", b'{"minutes":5}', 404),
        ("/v1/users/missing/status", "GET", None, 404),
        ("/v1/users/child/no-such-action", "POST", b"{}", 404),
        ("/v1/users/child/add-time", "POST", b"x" * 17000, 413),
        ("/v1/users/child/add-time", "GET", None, 404),
    ],
)
def test_http_validation(endpoint, path, method, body, expected):
    connection = http.client.HTTPSConnection(
        "localhost",
        endpoint.port,
        context=ssl.create_default_context(cafile=endpoint.cert),
        timeout=3,
    )
    try:
        connection.request(method, path, body, {**HEADERS, "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == expected
        assert "error" in json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Content-Type": "text/plain"}, 415),
        ({"Idempotency-Key": ""}, 400),
        ({"Content-Length": "-1"}, 400),
        ({"Transfer-Encoding": "chunked"}, 400),
    ],
)
def test_invalid_headers(endpoint, headers, status):
    connection = http.client.HTTPSConnection(
        "localhost",
        endpoint.port,
        context=ssl.create_default_context(cafile=endpoint.cert),
        timeout=3,
    )
    try:
        connection.request(
            "POST",
            "/v1/users/child/add-time",
            b'{"minutes":5}',
            {**HEADERS, "Content-Type": "application/json", **headers},
        )
        response = connection.getresponse()
        assert response.status == status
        response.read()
    finally:
        connection.close()


def test_stalled_tls_handshake_does_not_block_accept_loop(endpoint):
    with socket.create_connection(("localhost", endpoint.port), timeout=2):
        result = endpoint.client.request(endpoint.url + "/v1/users/child/status", headers=HEADERS)
        assert result["user"] == "child"


def test_enforcement_outage_is_accepted_pending_not_success(endpoint, setup):
    setup.enforcer.offline = True
    result = endpoint.client.request(
        endpoint.url + "/v1/users/child/add-time",
        method="POST",
        payload={"minutes": 5},
        headers=HEADERS,
    )
    assert result["application"]["status"] == "pending"


def test_unix_peer_is_resolved_to_its_own_user(setup, monkeypatch):
    seen = []

    def lookup(uid):
        seen.append(uid)
        return SimpleNamespace(pw_name="sibling")

    monkeypatch.setattr(pwd, "getpwuid", lookup)
    with serving(UnixServer(setup.service)):
        result = unix_request(setup.config.agent.socketPath, "/v1/request-time", {"minutes": 15})
        assert result["user"] == "sibling" and result["pendingRequest"]
        assert setup.service.status("child")["pendingRequest"] is None
        with pytest.raises(RemoteError) as error:
            unix_request(setup.config.agent.socketPath, "/v1/users/child/add-time", {"minutes": 15})
        assert error.value.status == 404
    assert seen and all(uid == os.getuid() for uid in seen)


def test_unmanaged_unix_peer_is_denied(setup):
    with serving(UnixServer(setup.service)):
        with pytest.raises(RemoteError) as error:
            unix_request(setup.config.agent.socketPath, "/v1/status")
        assert error.value.status == 403


def test_writable_socket_directory_is_rejected(setup, tmp_path):
    tmp_path.chmod(0o770)
    with pytest.raises(ValueError, match="writable"):
        UnixServer(setup.service)


def test_https_redirect_is_not_followed_with_credentials(endpoint, monkeypatch):
    from family_controls.adapters.http import Handler

    def redirect(self):
        self.send_response(302)
        self.send_header("Location", "https://unexpected.example/token-sink")
        self.send_header("Content-Length", "0")
        self.end_headers()

    monkeypatch.setattr(Handler, "do_GET", redirect)
    with pytest.raises(RemoteError, match="Redirects"):
        endpoint.client.request(endpoint.url + "/redirect", headers=HEADERS)
