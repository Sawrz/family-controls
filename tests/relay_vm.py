"""Chat interfaces against the actual backend VM; external chat transports are simulated."""

import asyncio
import time
from types import SimpleNamespace

from family_controls.adapters.client import Gateway
from family_controls.adapters.matrix import Matrix
from family_controls.adapters.sqlite import Repository
from family_controls.adapters.telegram import Relay
from family_controls.clock import SystemClock
from family_controls.config import BackendConnection, MatrixConfig, RelayConfig


def connection(token):
    return BackendConnection(
        url="https://backend:18450",
        tokenFile=token,
        caFile="/tmp/backend-ca.crt",
    )


class TelegramTransport:
    def __init__(self):
        self.sent = []

    def request(self, url, *, payload, **kwargs):
        if url.endswith("getChatMember"):
            return {"ok": True, "result": {"status": "administrator"}}
        if url.endswith("sendMessage") or url.endswith("editMessageText"):
            self.sent.append(payload)
        return {"ok": True, "result": {"message_id": 1}}


def test_interfaces_use_central_identity_and_queue(tmp_path):
    repository = Repository(str(tmp_path / "telegram.sqlite"))
    config = RelayConfig(
        parentGroupChatId="-42",
        children=["child"],
        backend=BackendConnection(socketPath="/run/family-controls-backend/adapters.sock"),
    )
    gateway = Gateway(config.backend)
    relay = Relay(config, "bot", gateway, repository, SystemClock("UTC"), TelegramTransport())
    update = {
        "update_id": 51,
        "message": {"chat": {"id": -42}, "from": {"id": 501}, "text": "/addtime child 5m"},
    }
    relay.handle(update)
    relay.handle(update)
    matrix_config = MatrixConfig(
        backend=connection("/etc/matrix-interface-token"),
        children=["child"],
        homeserver="https://matrix.test",
        userId="@bot:test",
        deviceId="test",
        tokenFile="/unused",
        roomId="!room:test",
    )
    matrix = Matrix(matrix_config, Gateway(matrix_config.backend), repository, SimpleNamespace())
    matrix.execute("!addtime child 7m", "@parent:test", "matrix-vm-once")
    matrix.execute("!addtime child 7m", "@parent:test", "matrix-vm-once")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = gateway.status("child")
        if status["effectiveDailyLimitMinutes"] == 72 and status["delivery"]["pending"] == 0:
            break
        time.sleep(0.5)
    assert status["effectiveDailyLimitMinutes"] == 72
    assert status["application"]["status"] == "applied"
    assert all(event["details"]["actor"] == "admin" for event in gateway.history("child")[:2])
    gateway.language("telegram:501", "me", "de")
    assert "Sprache" in matrix.execute("!language de", "@parent:test", "language")
    relay.handle(
        {"update_id": 52, "message": {"chat": {"id": -42}, "from": {"id": 501}, "text": "/family"}}
    )
    assert (
        relay.client.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Zeit hinzufügen"
    )
    matrix.config.children = ["child", "sibling"]
    confirmation = matrix.execute("!addtime all 15m", "@parent:test", "bulk-prepare")
    assert "jeweils 15 Minuten" in confirmation
    identifier = confirmation.split("!confirm ")[1]
    matrix.execute("!confirm " + identifier, "@parent:test", "bulk-confirm")
    matrix.execute("!confirm " + identifier, "@parent:test", "bulk-retry")
    assert gateway.status("child")["effectiveDailyLimitMinutes"] == 87
    assert gateway.status("sibling")["effectiveDailyLimitMinutes"] == 75
    gateway.command("child", "deduct-time", {"minutes": 15}, "bulk-restore-child", "telegram:501")
    gateway.command("sibling", "restore-today", {}, "bulk-restore-sibling", "telegram:501")
    gateway.language("telegram:501", "me", "en")
    repository.close()


def test_pending_request_reaches_both_destinations_over_sse(tmp_path):
    """The Nix driver first creates a real sibling request on the managed device."""
    repository = Repository(str(tmp_path / "notifications.sqlite"))
    config = RelayConfig(
        parentGroupChatId="-42",
        children=["sibling"],
        backend=BackendConnection(socketPath="/run/family-controls-backend/adapters.sock"),
    )
    transport = TelegramTransport()
    gateway = Gateway(config.backend)
    relay = Relay(config, "bot", gateway, repository, SystemClock("UTC"), transport)
    matrix_config = MatrixConfig(
        backend=connection("/etc/matrix-interface-token"),
        children=["sibling"],
        homeserver="https://matrix.test",
        userId="@bot:test",
        deviceId="test",
        tokenFile="/unused",
        roomId="!room:test",
    )
    messages = []

    async def send(room, kind, body, tx_id):
        messages.append((room, body["body"]))
        return SimpleNamespace(event_id="$sent")

    matrix = Matrix(
        matrix_config,
        Gateway(matrix_config.backend),
        repository,
        SimpleNamespace(
            rooms={"!room:test": SimpleNamespace(encrypted=True)},
            device_store={},
            room_send=send,
        ),
    )
    expected = gateway.status("sibling")["pendingRequest"]
    for interface in (relay, matrix):
        stream = interface.gateway.client.events("/api/v1/events", 0)
        deadline = time.monotonic() + 30
        try:
            for event in stream:
                assert time.monotonic() < deadline, "Request event was not delivered"
                if (
                    event
                    and event["kind"] == "request.changed"
                    and event["data"]["child"] == "sibling"
                ):
                    assert event["data"]["request"]["id"] == expected["id"]
                    if interface is relay:
                        relay.event(event)
                    else:
                        asyncio.run(matrix.notification(event))
                    break
            else:
                raise AssertionError("Event stream ended before the request")
        finally:
            stream.close()
    assert len(transport.sent) == len(messages) == 1
    assert str(transport.sent[0]["chat_id"]) == "-42"
    assert "Sibling requests" in transport.sent[0]["text"]
    assert transport.sent[0]["reply_markup"]["inline_keyboard"]
    assert messages[0][0] == "!room:test"
    assert "Sibling requests 15 minutes" in messages[0][1]
    assert expected["id"] in messages[0][1]
    matrix.execute(
        f"!approve sibling {expected['id']} 15m", "@parent:test", "matrix-notification-decision"
    )
    seen = set()
    stream = gateway.client.events("/api/v1/events", 0)
    deadline = time.monotonic() + 30
    try:
        for event in stream:
            assert time.monotonic() < deadline, "Decision did not reach both adapters"
            if (
                event
                and event["data"].get("operationId") == "matrix-notification-decision"
                and event["kind"] in {"request.changed", "command.applied"}
            ):
                relay.event(event)
                asyncio.run(matrix.notification(event))
                seen.add(event["kind"])
                if len(seen) == 2:
                    break
    finally:
        stream.close()
    assert seen == {"request.changed", "command.applied"}
    assert transport.sent[-1]["message_id"] == 1
    assert "Applied on all computers" in transport.sent[-1]["text"]
    assert "Applied on all computers" in messages[-1][1]
    assert "Today's budget" not in transport.sent[-1]["text"]
    repository.close()
