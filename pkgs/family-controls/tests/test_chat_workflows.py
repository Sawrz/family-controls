"""Language precedence, bulk safety and cross-interface request lifecycle."""

import asyncio
from datetime import timedelta

import pytest
from family_controls.adapters.client import Gateway as BackendGateway
from family_controls.backend.models import ProfileUpdate
from family_controls.domain import InvalidCommand
from family_controls.presentation import minutes_text, status_text, timestamp
from test_backend import agent
from test_backend import central as central
from test_matrix import matrix as matrix
from test_telegram import click, message
from test_telegram import relay as relay


def test_personal_language_and_shared_language_have_independent_precedence(relay):
    chat = relay.adapter.chat
    relay.gateway.language_preferences["defaultLanguage"] = "de"
    assert chat.language("telegram:7") == chat.language() == "de"
    chat.change_language("telegram:7", "me", "en")
    assert chat.language("telegram:7") == "en" and chat.language() == "de"
    chat.change_language("telegram:7", "chat", "en")
    assert chat.language() == "en"
    chat.change_language("telegram:7", "me", "default")
    assert chat.language("telegram:7") == "de"
    with pytest.raises(InvalidCommand):
        chat.change_language("telegram:7", "me", "xx")


def test_bulk_confirmation_is_owner_bound_and_retries_only_once_per_child(relay):
    chat = relay.adapter.chat
    confirmation, text = chat.confirmation(
        ["child", "sibling"], "add-time", {"minutes": 15}, "telegram:7"
    )
    assert "15 minutes each" in text and "Child, Sibling" in text
    with pytest.raises(InvalidCommand):
        chat.confirm(confirmation, "telegram:8")
    relay.gateway.offline.add("sibling")
    assert "Retry" in chat.confirm(confirmation, "telegram:7")
    relay.gateway.offline.clear()
    assert "Retry" not in chat.confirm(confirmation, "telegram:7")
    assert relay.gateway.status("child")["effectiveDailyLimitMinutes"] == 75
    assert relay.gateway.status("sibling")["effectiveDailyLimitMinutes"] == 75
    relay.setup.clock.advance(minutes=11)
    with pytest.raises(InvalidCommand):
        chat.confirm(confirmation, "telegram:7")


def test_telegram_menu_edits_in_place_and_rejects_another_parent(relay):
    relay.adapter.handle(message("/family"))
    rows = relay.transport.sent[-1][1]["reply_markup"]["inline_keyboard"]
    assert [r[0]["text"] for r in rows] == ["Add time", "Extend allowed hours", "More"]
    callback = click(rows[0][0])
    callback["callback_query"]["message"]["message_id"] = 12
    relay.adapter.handle(callback)
    method, selection = relay.transport.sent[-1]
    assert method == "editMessageText" and selection["message_id"] == 12
    relay.adapter.handle(click(selection["reply_markup"]["inline_keyboard"][0][0], sender=8))
    assert "another parent" in relay.transport.sent[-1][1]["text"]
    assert not relay.gateway.calls


def test_matrix_bulk_command_requires_confirmation(matrix):
    matrix.config.children = ["child", "sibling"]
    result = matrix.execute("!addtime all 15m", "@parent:test", "prepare")
    assert "15 minutes each" in result
    assert matrix.gateway.status("child")["effectiveDailyLimitMinutes"] == 60
    token = result.split("!confirm ")[1]
    matrix.execute("!confirm " + token, "@parent:test", "commit")
    matrix.execute("!confirm " + token, "@parent:test", "retry")
    assert matrix.gateway.status("child")["effectiveDailyLimitMinutes"] == 75
    assert matrix.gateway.status("sibling")["effectiveDailyLimitMinutes"] == 75


def test_backend_language_permissions_and_web_account_preference(central):
    c = central.client
    central.store.put("identities", "telegram:7", {"username": "admin"})
    adapter = {"Authorization": "Bearer test-telegram-token", "X-Family-Actor": "telegram:7"}
    assert (
        c.put(
            "/api/v1/preferences", json={"defaultLanguage": "de", "chatLanguages": {}}
        ).status_code
        == 200
    )
    assert (
        c.put(
            "/api/v1/adapter/language", headers=adapter, json={"scope": "me", "language": "en"}
        ).status_code
        == 200
    )
    assert c.get("/api/v1/me").json()["language"] == "en"
    central.accounts.profile("admin", ProfileUpdate(language="de"))
    assert c.get("/api/v1/me", headers=adapter).json()["language"] == "de"
    assert (
        c.put("/api/v1/preferences", headers=adapter, json={"defaultLanguage": "en"}).status_code
        == 403
    )
    account = central.store.get("accounts", "admin")
    account["role"] = "parent"
    central.store.put("accounts", "admin", account)
    assert (
        c.put(
            "/api/v1/adapter/language", headers=adapter, json={"scope": "chat", "language": "en"}
        ).status_code
        == 403
    )
    assert (
        c.put(
            "/api/v1/adapter/language", headers=adapter, json={"scope": "me", "language": None}
        ).status_code
        == 200
    )


def test_web_decision_updates_original_chat_cards_and_delivery_receipt(
    central, relay, matrix, monkeypatch
):
    gateway = BackendGateway(relay.adapter.config.backend)

    def request(route, payload=None, *, method=None, headers=None):
        response = central.client.request(
            method or ("POST" if payload is not None else "GET"), route, json=payload
        )
        assert response.is_success, response.text
        return response.json()

    monkeypatch.setattr(gateway.client, "request", request)
    relay.adapter.gateway = relay.adapter.chat.gateway = gateway
    matrix.gateway = matrix.chat.gateway = gateway
    sync = agent(central)
    pending = central.setup.service.execute(
        "child", "request-time", {"minutes": 20, "reason": "Finish homework"}, "request", "unix:123"
    )["pendingRequest"]
    sync.exchange()
    event = next(e for e in central.store.events("parents", 0) if e["kind"] == "request.changed")
    relay.adapter.event(event)
    asyncio.run(matrix.notification(event))
    original = relay.transport.sent[-1][1]
    assert "Finish homework" in original["text"]
    central.backend.command(
        "child",
        "decide-request",
        {"requestId": pending["id"], "decision": "grant-session", "minutes": 15},
        "web-decision",
        "admin",
    )
    decided = next(
        e for e in central.store.events("parents", event["id"]) if e["kind"] == "request.changed"
    )
    relay.adapter.event(decided)
    asyncio.run(matrix.notification(decided))
    assert relay.transport.sent[-1][0] == "editMessageText"
    text = relay.transport.sent[-1][1]["text"]
    assert "approved" in text and "15 min" in text and "Waiting" in text
    assert "Today's budget" not in text
    assert relay.transport.sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Menu"
    sync.exchange()
    sync.exchange()
    applied = next(
        e for e in central.store.events("parents", decided["id"]) if e["kind"] == "command.applied"
    )
    relay.adapter.event(applied)
    asyncio.run(matrix.notification(applied))
    assert "Applied on all" in relay.transport.sent[-1][1]["text"]
    assert "Applied on all" in matrix.client.messages[-1][0]


def test_expiry_emits_once_and_schedule_uses_actual_effective_windows(central):
    central.backend.command("child", "extend-access", {"minutes": 30}, "extend", "admin")
    status = central.backend.status("child")
    assert status["schedule"]["regular"] != status["schedule"]["today"]
    text = status_text("child", status, "de", central.setup.clock.now())
    assert "Reguläre Zeiten" in text and "Heute erlaubte Zeiten" in text
    assert "Access until" not in text and "60.0" not in text
    central.setup.clock.advance(days=1)
    central.backend.reconcile_notifications()
    central.backend.reconcile_notifications()
    assert (
        len([e for e in central.store.events("parents", 0) if e["kind"] == "command.expired"]) == 1
    )


@pytest.mark.parametrize(
    "seconds,expected",
    [(59, "Weniger als eine Minute"), (60, "1 min"), (5400, "90 min"), (None, "Unbekannt")],
)
def test_chat_minutes_do_not_expose_floats(seconds, expected):
    assert minutes_text(seconds, "de") == expected


def test_timestamp_uses_local_calendar_day(central):
    now = central.setup.clock.now()
    assert timestamp(now.isoformat(), "de", "Europe/Berlin", now).startswith("Heute,")
    assert timestamp((now - timedelta(days=1)).isoformat(), "de", "Europe/Berlin", now).startswith(
        "Gestern,"
    )


def test_matrix_reply_to_card_resolves_exact_request_without_copying_id(matrix, setup):
    import time
    from types import SimpleNamespace

    pending = setup.service.execute("child", "request-time", {"minutes": 20}, "request", "unix:1")[
        "pendingRequest"
    ]
    asyncio.run(
        matrix.notification(
            {"id": 1, "kind": "request.changed", "data": {"child": "child", "request": pending}}
        )
    )

    async def reply():
        await matrix.receive(
            SimpleNamespace(room_id=matrix.config.roomId),
            SimpleNamespace(
                sender="@parent:test",
                body="> Bot request\n\n!approve 30m",
                event_id="$reply",
                server_timestamp=int(time.time() * 1000),
                decrypted=True,
                source={"content": {"m.relates_to": {"m.in_reply_to": {"event_id": "$sent"}}}},
            ),
        )
        await matrix.drain()

    asyncio.run(reply())
    status = matrix.gateway.status("child")
    assert status["request"]["status"] == "approved"
    assert status["effectiveDailyLimitMinutes"] == 90


def test_telegram_selected_children_custom_duration_confirmation_journey(relay):
    def press(label):
        rows = relay.transport.sent[-1][1]["reply_markup"]["inline_keyboard"]
        button = next(b for row in rows for b in row if b["text"] == label)
        update = click(button, update=len(relay.transport.sent) + 1)
        update["callback_query"]["message"]["message_id"] = 10
        relay.adapter.handle(update)

    relay.adapter.handle(message("/family"))
    press("Add time")
    press("All children")
    press("Continue")
    press("Custom…")
    prompt = relay.transport.sent[-1][1]
    assert prompt["reply_markup"] == {"force_reply": True}
    relay.adapter.handle(message("23m", update=50, reply=len(relay.transport.sent)))
    assert "23 minutes each" in relay.transport.sent[-1][1]["text"]
    assert not relay.gateway.calls
    press("Confirm")
    assert all(
        relay.gateway.status(c)["effectiveDailyLimitMinutes"] == 83 for c in ("child", "sibling")
    )
    assert "What would you like to do?" in relay.transport.sent[-1][1]["text"]


def test_unlinked_sender_gets_localized_recovery_without_stopping_other_commands(
    relay, matrix, monkeypatch
):
    import time

    def unlinked(actor):
        raise InvalidCommand("Messaging account is not linked")

    relay.gateway.language_preferences["defaultLanguage"] = "de"
    matrix.gateway.language_preferences["defaultLanguage"] = "de"
    monkeypatch.setattr(relay.gateway, "authorize", unlinked)
    monkeypatch.setattr(matrix.gateway, "authorize", unlinked)
    relay.adapter.handle(message("/family"))
    assert "Aktion nicht ausgeführt" in relay.transport.sent[-1][1]["text"]
    matrix.repository.relay_set(
        "matrix-inbox:$unlinked",
        {"sender": "@stranger:test", "text": "!family", "received": time.time()},
    )
    asyncio.run(matrix.drain())
    assert "Aktion nicht ausgeführt" in matrix.client.messages[-1][0]
    assert not matrix.repository.relay_items("matrix-inbox:")


def test_german_expired_delivery_and_linking_use_household_fallback(matrix, monkeypatch):
    import time

    matrix.gateway.language_preferences["defaultLanguage"] = "de"
    monkeypatch.setattr(matrix.gateway, "claim_link", lambda *args: None, raising=False)
    assert "Mein Konto" in matrix.execute("!link code", "@new:test", "link")
    matrix.repository.relay_set(
        "matrix-inbox:$expired",
        {"sender": "@parent:test", "text": "!addtime child 5m", "received": time.time() - 601},
    )
    asyncio.run(matrix.drain())
    assert "abgelaufen" in matrix.client.messages[-1][0]
    assert not matrix.gateway.calls
