"""Exercise Telegram presentation against the real application and durable state."""

from types import SimpleNamespace

import pytest
from family_controls.adapters.telegram import Relay
from family_controls.config import RelayConfig
from family_controls.domain import Unavailable


class Telegram:
    def __init__(self):
        self.sent = []
        self.updates = []
        self.admins = {7, 8}
        self.fail_send = False

    def request(self, url, *, payload, **kwargs):
        method = url.rsplit("/", 1)[-1]
        self.sent.append((method, payload))
        if method == "getChatMember":
            return {
                "ok": True,
                "result": {
                    "status": "administrator" if payload["user_id"] in self.admins else "member"
                },
            }
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [u.copy() for u in self.updates if u["update_id"] >= payload["offset"]],
            }
        if method == "sendMessage" and self.fail_send:
            raise Unavailable("Telegram offline")
        return {"ok": True, "result": {"message_id": len(self.sent)}}


class Gateway:
    def __init__(self, service):
        self.service = service
        self.offline = set()
        self.calls = []

    def authorize(self, actor):
        return {"username": actor}

    def history(self, child):
        return self.service.repository.history(child)

    def status(self, child):
        if child in self.offline:
            raise Unavailable("Device offline")
        return self.service.status(child)

    def command(self, child, action, body, operation, actor):
        self.calls.append((child, action, body, operation, actor))
        if child in self.offline:
            raise Unavailable("Device offline")
        return self.service.execute(child, action, body, operation, actor)


@pytest.fixture
def relay(setup):
    transport = Telegram()
    gateway = Gateway(setup.service)
    config = RelayConfig.model_validate(
        {
            "parentGroupChatId": "-42",
            "children": ["child", "sibling"],
            "backend": {"url": "https://backend", "tokenFile": "/unused"},
        }
    )
    adapter = Relay(config, "test-token", gateway, setup.repository, setup.clock, transport)
    return SimpleNamespace(adapter=adapter, transport=transport, gateway=gateway, setup=setup)


def message(text, update=1, sender=7, chat=-42, reply=None):
    return {
        "update_id": update,
        "message": {
            "chat": {"id": chat},
            "from": {"id": sender},
            "text": text,
            "reply_to_message": {"message_id": reply},
        },
    }


def click(button, update=1, sender=7):
    return {
        "update_id": update,
        "callback_query": {
            "id": str(update),
            "data": button["callback_data"],
            "from": {"id": sender},
            "message": {"chat": {"id": -42}},
        },
    }


def texts(relay):
    return [p["text"] for m, p in relay.transport.sent if m == "sendMessage"]


@pytest.mark.parametrize("chat,sender", [(-41, 7), (-42, 9)])
def test_only_current_group_administrators_can_change_policy(relay, chat, sender):
    relay.adapter.handle(message("/addtime child 25m", chat=chat, sender=sender))
    assert not relay.gateway.calls


def test_custom_prompt_binds_sender_reply_and_expires(relay):
    relay.adapter.handle(message("/addtime CHILD"))
    prompt_id = len(relay.transport.sent)
    assert relay.transport.sent[-1][1]["reply_markup"] == {"force_reply": True}
    relay.adapter.handle(message("25m", update=2, sender=8, reply=prompt_id))
    assert not relay.gateway.calls
    relay.adapter.handle(message("1h 10m", update=3, reply=prompt_id))
    assert relay.setup.service.status("child")["effectiveDailyLimitMinutes"] == 130
    relay.adapter.handle(message("25m", update=4, reply=prompt_id))
    assert len(relay.gateway.calls) == 1
    relay.adapter.handle(message("/until child", update=5))
    prompt_id = len(relay.transport.sent)
    relay.setup.clock.advance(minutes=11)
    relay.adapter.handle(message("19:00", update=6, reply=prompt_id))
    assert len(relay.gateway.calls) == 1


def test_two_parents_clicking_same_grant_apply_once_with_original_actor(relay):
    button = relay.adapter.button("25m", "child", "add-time", {"minutes": 25})
    relay.adapter.handle(click(button))
    relay.adapter.handle(click(button, update=2, sender=8))
    assert relay.setup.service.status("child")["effectiveDailyLimitMinutes"] == 85
    assert [call[-1] for call in relay.gateway.calls] == ["telegram:7", "telegram:7"]


def test_stale_approval_cannot_approve_a_later_request(relay):
    service = relay.setup.service
    status = service.execute("child", "request-time", {}, "request-1", "child")
    button = next(
        button
        for row in relay.adapter.keyboard("child", status)
        for button in row
        if button["text"] == "Deny request"
    )
    relay.adapter.handle(click(button))
    relay.setup.clock.advance(seconds=61)
    newer = service.execute("child", "request-time", {}, "request-2", "child")["pendingRequest"]
    relay.adapter.handle(click(button, update=3))
    assert service.status("child")["pendingRequest"]["id"] == newer["id"]


def test_child_selector_for_unlimited_requires_confirmation(relay):
    relay.adapter.handle(message("/unlimited"))
    selector = relay.transport.sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]
    relay.adapter.handle(click(selector, update=2))
    assert not relay.gateway.calls
    confirmation = relay.transport.sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]
    relay.adapter.handle(click(confirmation, update=3))
    assert relay.setup.service.status("child")["unlimitedToday"]


@pytest.mark.parametrize(
    "command,expected",
    [("/setremaining child 0", 0), ("/setlimit child 2h", 120), ("/deducttime child 25m", 35)],
)
def test_duration_commands(relay, command, expected):
    relay.adapter.handle(message(command))
    assert relay.setup.service.status("child")["effectiveDailyLimitMinutes"] == expected


def test_durable_inbox_does_not_block_other_children_and_survives_restart(relay):
    relay.gateway.offline.add("child")
    relay.transport.updates = [
        message("/addtime child 25m"),
        message("/addtime sibling 15m", update=2),
    ]
    relay.adapter.poll_once()
    state = relay.setup.repository.relay_get("transport")
    assert state["offset"] == 3 and len(state["pending"]) == 1
    assert relay.setup.service.status("sibling")["effectiveDailyLimitMinutes"] == 75
    restarted = Relay(
        relay.adapter.config,
        "test-token",
        relay.gateway,
        relay.setup.repository,
        relay.setup.clock,
        relay.transport,
    )
    relay.gateway.offline.clear()
    restarted.poll_once()
    assert relay.setup.service.status("child")["effectiveDailyLimitMinutes"] == 85
    assert not relay.setup.repository.relay_get("transport")["pending"]


def test_successful_grant_with_failed_telegram_reply_is_not_repeated(relay):
    relay.transport.updates = [message("/addtime child 25m")]
    relay.transport.fail_send = True
    relay.adapter.poll_once()
    assert len(relay.setup.repository.relay_get("transport")["pending"]) == 1
    relay.transport.fail_send = False
    relay.adapter.poll_once()
    assert relay.setup.service.status("child")["effectiveDailyLimitMinutes"] == 85


def test_queued_offline_command_expires_instead_of_granting_next_day(relay):
    relay.gateway.offline.add("child")
    relay.transport.updates = [message("/addtime child 25m")]
    relay.adapter.poll_once()
    relay.setup.clock.advance(minutes=11)
    relay.gateway.offline.clear()
    relay.adapter.poll_once()
    assert relay.setup.service.status("child")["effectiveDailyLimitMinutes"] == 60
    assert "expired" in texts(relay)[-1]


def test_request_event_does_not_query_an_offline_sibling(relay):
    relay.gateway.offline.add("child")
    request = relay.setup.service.execute(
        "sibling", "request-time", {"reason": "Finish homework"}, "r", "child"
    )["pendingRequest"]
    relay.adapter.event(
        {
            "kind": "request.changed",
            "data": {"child": "sibling", "request": request},
        }
    )
    assert len(texts(relay)) == 1 and "Finish homework" in texts(relay)[0]


def test_prompt_cancellation_and_invalid_input_do_not_mutate(relay):
    relay.adapter.handle(message("/addtime child"))
    prompt_id = len(relay.transport.sent)
    relay.adapter.handle(message("negative", update=2, reply=prompt_id))
    relay.adapter.handle(message("/cancel", update=3, reply=prompt_id))
    relay.adapter.handle(message("25m", update=4, reply=prompt_id))
    assert not relay.gateway.calls


def test_expired_button_and_help_are_safe(relay):
    button = relay.adapter.button("Add", "child", "add-time", {"minutes": 25})
    relay.setup.clock.advance(days=1)
    relay.adapter.handle(click(button))
    relay.adapter.handle(message("/help", update=2))
    assert not relay.gateway.calls
    assert "expired" in texts(relay)[0]
    assert "/family" in texts(relay)[1]


@pytest.mark.parametrize(
    "text",
    ["/unlimited child tomorrow", "/block child today tomorrow", "/addtime child 25m until=19:00"],
)
def test_unexpected_arguments_cannot_silently_change_scope(relay, text):
    relay.adapter.handle(message(text))
    assert not relay.gateway.calls


def request_button(relay, label):
    status = relay.setup.service.execute(
        "child", "request-time", {"minutes": 25}, "request", "child"
    )
    return next(
        button
        for row in relay.adapter.keyboard("child", status)
        for button in row
        if button["text"] == label
    )


def test_all_day_request_approval_has_confirmation_and_resolves_request(relay):
    button = request_button(relay, "Approve rest of day…")
    relay.adapter.handle(click(button))
    assert not relay.setup.service.status("child")["unlimitedToday"]
    confirmation = relay.transport.sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]
    relay.adapter.handle(click(confirmation, update=2))
    status = relay.setup.service.status("child")
    assert status["request"]["status"] == "approved"
    assert status["unlimitedToday"] and status["allowedUntil"] == "24:00"


def test_denial_reason_and_parent_history_are_accessible(relay):
    button = request_button(relay, "Deny with reason…")
    relay.adapter.handle(click(button))
    prompt_id = len(relay.transport.sent)
    relay.adapter.handle(message("Dinner time", update=2, reply=prompt_id))
    status = relay.setup.service.status("child")
    assert status["request"]["status"] == "denied"
    assert status["request"]["decision"] == "Dinner time"
    relay.adapter.handle(message("/history child", update=3))
    assert "telegram:7" in texts(relay)[-1] and "applied" in texts(relay)[-1]
