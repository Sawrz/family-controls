"""Real HTTPS and SSE: certificate verification, replay and event delivery."""

import socket
import threading
import time

import pytest
import uvicorn
from family_controls.adapters.client import BackendClient
from family_controls.config import BackendConnection
from family_controls.domain import Unavailable
from test_backend import central as central
from test_protocols import certificate as certificate


@pytest.fixture
def server(central, certificate):
    cert, key = certificate
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            central.app,
            host="127.0.0.1",
            port=port,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            log_level="error",
            ws="none",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    client = BackendClient(
        BackendConnection(url=f"https://localhost:{port}", caFile=str(cert)),
        central.client.headers["Authorization"][7:],
    )
    try:
        yield client
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_real_sse_replays_from_cursor_without_consuming_another_interface(central, server):
    central.backend.command("child", "add-time", {"minutes": 5}, "event-one", "admin")
    stream = server.events("/api/v1/events", 0)
    first = next(stream)
    stream.close()
    assert first["kind"] == "command.queued"
    central.backend.command("sibling", "add-time", {"minutes": 7}, "event-two", "admin")
    resumed = server.events("/api/v1/events", first["id"])
    second = next(resumed)
    resumed.close()
    assert second["data"]["operationId"] == "event-two"
    independent = server.events("/api/v1/events", 0)
    assert next(independent) == first
    independent.close()
    assert server.request("/api/v1/children")["children"] == ["child", "sibling"]


def test_untrusted_tls_fails_and_api_commands_cross_real_https(server):
    with pytest.raises(Unavailable):
        BackendClient(BackendConnection(url=server.config.url), server.credential).request(
            "/health"
        )
    response = server.request(
        "/api/v1/children/child/commands/add-time",
        {"minutes": 5},
        headers={"Idempotency-Key": "https"},
    )
    assert response["operation"]["status"] == "queued"


def test_failed_notification_replays_before_advancing_durable_cursor(setup, monkeypatch):
    from family_controls.adapters.events import consume_events

    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda _seconds: False)
    attempts, delivered = [], []

    class Client:
        def events(self, route, cursor):
            attempts.append(cursor)
            yield {"id": 1}
            yield {"id": 2}

    def deliver(event):
        if len(attempts) == 1:
            raise Unavailable("Transport interrupted")
        delivered.append(event["id"])
        if event["id"] == 2:
            stop.set()

    consume_events(Client(), setup.repository, deliver, stop)
    assert attempts == [0, 0] and delivered == [1, 2]
    assert setup.repository.relay_get("event-cursor") == {"id": 2}
