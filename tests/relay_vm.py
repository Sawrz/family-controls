"""Run on the relay VM against the managed VM; only Telegram is substituted."""

from pathlib import Path

import pytest
from family_controls.adapters.http import Gateway
from family_controls.adapters.sqlite import Repository
from family_controls.adapters.telegram import Relay
from family_controls.bootstrap import SystemClock
from family_controls.config import RelayConfig


class Telegram:
    def __init__(self, updates=()):
        self.messages = []
        self.updates = updates

    def request(self, url, *, payload, **kwargs):
        method = url.rsplit("/", 1)[-1]
        if method == "getChatMember":
            return {"ok": True, "result": {"status": "administrator"}}
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [u for u in self.updates if u["update_id"] >= payload["offset"]],
            }
        self.messages.append((method, payload))
        return {"ok": True, "result": {"message_id": len(self.messages)}}


@pytest.fixture
def config():
    return RelayConfig.model_validate(
        {
            "parentGroupChatId": "-42",
            "hostChildren": {
                "Kid": {
                    "host": "machine",
                    "port": 18451,
                    "user": "child",
                    "deviceId": "existing-id",
                    "apiTokenFile": "/etc/family-test-token",
                    "caFile": "/etc/family-test-ca.crt",
                }
            },
        }
    )


def test_telegram_button_through_verified_https_to_real_timekpr(tmp_path, config):
    gateway = Gateway(config)
    before = gateway.status("Kid")["effectiveDailyLimitMinutes"]
    repository = Repository(str(tmp_path / "relay.sqlite"))
    transport = Telegram()
    relay = Relay(config, "test-bot-token", gateway, repository, SystemClock("UTC"), transport)
    try:
        button = relay.button("Add 25m", "Kid", "add-time", {"minutes": 25})
        for update, parent in ((91, 501), (92, 502)):
            relay.handle(
                {
                    "update_id": update,
                    "callback_query": {
                        "id": str(update),
                        "data": button["callback_data"],
                        "from": {"id": parent},
                        "message": {"chat": {"id": -42}},
                    },
                }
            )
        status = gateway.status("Kid")
        assert status["effectiveDailyLimitMinutes"] == before + 25
        assert status["application"]["status"] == "applied"
        assert len([method for method, _ in transport.messages if method == "sendMessage"]) == 2
        history = gateway.request("Kid", "history")["events"]
        assert history[0]["details"]["actor"] == "telegram:501"
        assert history[0]["outcome"] == "applied"
    finally:
        gateway.command("Kid", "set-limit", {"minutes": int(before)}, "restore-vm-relay", "test")
        repository.close()


@pytest.fixture
def durable_relay(config):
    """Reopen the same inbox before and after the test driver reboots this VM."""
    repository = Repository("/var/lib/family-controls-relay/recovery.sqlite")
    transport = Telegram(
        [
            {
                "update_id": 120,
                "message": {
                    "chat": {"id": -42},
                    "from": {"id": 501},
                    "text": "/addtime Kid 7m",
                },
            }
        ]
    )
    relay = Relay(
        config, "test-bot-token", Gateway(config), repository, SystemClock("UTC"), transport
    )
    try:
        yield relay
    finally:
        repository.close()


def test_offline_update_is_durably_queued(durable_relay):
    assert durable_relay.repository.relay_get("transport") is None
    durable_relay.poll_once()
    state = durable_relay.repository.relay_get("transport")
    assert state["offset"] == 121
    assert [item["update_id"] for item in state["pending"]] == [120]
    Path("/var/lib/family-controls-relay/boot-id").write_text(
        Path("/proc/sys/kernel/random/boot_id").read_text()
    )


def test_recovery_after_reboot_applies_queued_grant_once(durable_relay):
    assert (
        Path("/var/lib/family-controls-relay/boot-id").read_text()
        != Path("/proc/sys/kernel/random/boot_id").read_text()
    )
    state = durable_relay.repository.relay_get("transport")
    assert [item["update_id"] for item in state["pending"]] == [120]
    assert durable_relay.gateway.status("Kid")["effectiveDailyLimitMinutes"] == 60
    durable_relay.poll_once()
    assert durable_relay.repository.relay_get("transport") == {"offset": 121, "pending": []}
    durable_relay.poll_once()
    status = durable_relay.gateway.status("Kid")
    assert status["effectiveDailyLimitMinutes"] == 67
    assert status["application"]["status"] == "applied"
