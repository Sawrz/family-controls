"""Check our Matrix runtime wiring against the packaged encrypted SDK."""

import asyncio
import importlib.util
import threading
from types import SimpleNamespace

import pytest
from family_controls.adapters import matrix
from family_controls.adapters.sqlite import Repository
from family_controls.config import MatrixConfig

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("nio") is None, reason="Run the packaged matrix-runtime check"
)


def test_runtime_restores_encrypted_device_joins_room_and_closes(tmp_path, monkeypatch):
    import nio

    token = tmp_path / "token"
    token.write_text("test-access-token")
    config = MatrixConfig.model_validate(
        {
            "backend": {"url": "https://backend.test", "tokenFile": str(token)},
            "children": ["child"],
            "homeserver": "https://matrix.test",
            "userId": "@bot:test",
            "deviceId": "BOT",
            "roomId": "!family:test",
            "stateDir": str(tmp_path),
            "tokenFile": str(token),
        }
    )
    joined, closed = [], []
    stopped = threading.Event()
    original_close = nio.AsyncClient.close

    async def join(client, room):
        assert client.user_id == config.userId and client.device_id == config.deviceId
        assert client.access_token == "test-access-token"
        assert client.olm is not None and client.store is not None
        joined.append(room)
        return nio.JoinResponse(room)

    async def sync(client, **kwargs):
        assert kwargs == {"timeout": 10000, "full_state": True}
        await asyncio.sleep(0)
        raise OSError("Simulated homeserver disconnect")

    async def close(client):
        closed.append(client)
        await original_close(client)

    def events(client, repository, deliver, stop):
        stop.wait(5)
        stopped.set()

    monkeypatch.setattr(nio.AsyncClient, "join", join)
    monkeypatch.setattr(nio.AsyncClient, "sync_forever", sync)
    monkeypatch.setattr(nio.AsyncClient, "close", close)
    monkeypatch.setattr(matrix, "Gateway", lambda _config: SimpleNamespace(client=None))
    monkeypatch.setattr(matrix, "consume_events", events)
    repository = Repository(str(tmp_path / "interface.sqlite"))
    try:
        with pytest.raises(OSError, match="disconnect"):
            asyncio.run(matrix.run(config, repository))
        assert joined == [config.roomId] and len(closed) == 1 and stopped.is_set()
    finally:
        repository.close()
