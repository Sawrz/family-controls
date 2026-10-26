"""Chat interfaces against the actual backend VM; external chat transports are simulated."""

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
    def request(self, url, *, payload, **kwargs):
        if url.endswith("getChatMember"):
            return {"ok": True, "result": {"status": "administrator"}}
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
    repository.close()
