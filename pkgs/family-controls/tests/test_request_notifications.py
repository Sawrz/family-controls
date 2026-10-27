"""Device requests reach both chat adapters through the persisted backend event."""

import asyncio

from test_backend import agent
from test_backend import central as central
from test_matrix import matrix as matrix
from test_telegram import relay as relay


def test_synced_request_notifies_both_channels_and_cancellation_replaces_cards(
    central, relay, matrix
):
    sync = agent(central)
    request = central.setup.service.execute(
        "child", "request-time", {"minutes": 20, "reason": "Finish homework"}, "request", "unix:123"
    )["pendingRequest"]
    sync.exchange()
    sync.exchange()
    events = [e for e in central.store.events("parents", 0) if e["kind"] == "request.changed"]
    assert len(events) == 1
    event = events[0]
    assert event["data"]["request"]["id"] == request["id"]
    relay.adapter.event(event)
    asyncio.run(matrix.notification(event))
    method, message = relay.transport.sent[-1]
    assert method == "sendMessage" and str(message["chat_id"]) == "-42"
    assert "Finish homework" in message["text"]
    assert any(
        button["text"] == "Approve 15 min"
        for row in message["reply_markup"]["inline_keyboard"]
        for button in row
    )
    text, transaction = matrix.client.messages[-1]
    assert "Child requests 20 minutes\nFinish homework" in text
    assert request["id"] in text and transaction

    central.setup.service.execute(
        "child", "cancel-request", {"requestId": request["id"]}, "cancel", "unix:123"
    )
    sync.exchange()
    cancelled = central.store.events("parents", event["id"])[-1]
    assert cancelled["data"]["request"]["status"] == "cancelled"
    relay.adapter.event(cancelled)
    asyncio.run(matrix.notification(cancelled))
    assert relay.transport.sent[-1][0] == "editMessageText"
    assert "cancelled" in relay.transport.sent[-1][1]["text"]
    assert "cancelled" in matrix.client.messages[-1][0]
