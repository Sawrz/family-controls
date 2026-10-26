"""Our Matrix commands, encrypted-room requirements and persistent inbox semantics."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from family_controls.adapters.matrix import Matrix
from family_controls.config import BackendConnection, MatrixConfig
from family_controls.domain import InvalidCommand, Unavailable
from test_telegram import Gateway


class Client:
    def __init__(self):
        self.rooms = {"!family:test": SimpleNamespace(encrypted=True)}
        self.device_store = {}
        self.messages = []
        self.offline = False

    async def room_send(self, room, kind, body, tx_id):
        if self.offline:
            raise Unavailable("Offline")
        self.messages.append((body["body"], tx_id))
        return SimpleNamespace(event_id="$sent")


@pytest.fixture
def matrix(setup):
    config = MatrixConfig(
        backend=BackendConnection(url="https://backend", tokenFile="/unused"),
        children=["child"],
        homeserver="https://matrix.test",
        userId="@bot:test",
        deviceId="bot",
        tokenFile="/unused",
        roomId="!family:test",
    )
    return Matrix(config, Gateway(setup.service), setup.repository, Client())


@pytest.mark.parametrize(
    "command,budget",
    [
        ("!addtime child 5m", 65),
        ("!setlimit child 90m", 90),
        ("!setremaining child 20m", 20),
        ("!deducttime child 5m", 55),
    ],
)
def test_custom_commands_share_application_contract(matrix, command, budget):
    matrix.execute(command, "@parent:test", "one")
    matrix.execute(command, "@parent:test", "one")
    assert matrix.gateway.status("child")["effectiveDailyLimitMinutes"] == budget


def test_matrix_approval_requires_exact_request_identity(matrix, setup):
    request = setup.service.execute("child", "request-time", {"minutes": 10}, "req", "unix:1")[
        "pendingRequest"
    ]
    matrix.execute(f"!approve child {request['id']} 10m", "@parent:test", "decision")
    assert matrix.gateway.status("child")["request"]["status"] == "approved"
    with pytest.raises(InvalidCommand):
        matrix.execute(f"!deny child {request['id']} no", "@parent:test", "other")


def test_matrix_delivery_failure_retains_command_and_retries_once(matrix):
    async def scenario():
        room = SimpleNamespace(room_id="!family:test")
        event = SimpleNamespace(
            sender="@parent:test",
            body="!addtime child 5m",
            event_id="$once",
            server_timestamp=int(time.time() * 1000),
            decrypted=True,
        )
        await matrix.receive(room, event)
        matrix.client.offline = True
        await matrix.drain()
        assert len(matrix.repository.relay_items("matrix-inbox:")) == 1
        matrix.client.offline = False
        await matrix.drain()
        await matrix.receive(room, event)
        assert matrix.repository.relay_items("matrix-inbox:") == []
        assert matrix.gateway.status("child")["effectiveDailyLimitMinutes"] == 65

    asyncio.run(scenario())


def test_encrypted_configuration_never_sends_to_an_unencrypted_room(matrix):
    matrix.client.rooms["!family:test"].encrypted = False
    with pytest.raises(Unavailable, match="encryption"):
        asyncio.run(matrix.send("Private request", "notification"))
    assert not matrix.client.messages


def test_matrix_request_notification_contains_stable_request_id(matrix):
    asyncio.run(
        matrix.notification(
            {
                "id": 8,
                "kind": "request.changed",
                "data": {
                    "child": "child",
                    "request": {
                        "id": "request-123",
                        "status": "pending",
                        "minutes": 20,
                        "reason": "Finish",
                    },
                },
            }
        )
    )
    text, _ = matrix.client.messages[-1]
    assert "!approve child request-123" in text and "Finish" in text


@pytest.mark.parametrize(
    "text",
    [
        "!addtime other 5m",
        "!until child",
        "!block child nonsense",
        "!unknown child",
        "!unblock child extra",
        "!approve child",
    ],
)
def test_matrix_bad_commands_cannot_change_policy(matrix, text):
    with pytest.raises(InvalidCommand):
        matrix.execute(text, "@parent:test", "bad")
    assert matrix.gateway.status("child")["revision"] == 0
