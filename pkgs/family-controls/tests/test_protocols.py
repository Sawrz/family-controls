import os
import pwd
import subprocess
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from family_controls.adapters.http import (
    RemoteError,
    UnixServer,
    unix_request,
)


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
