"""Telegram presentation and durable delivery; policy stays behind AgentGateway."""

import hashlib
import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from ..application import ADJUSTMENTS as DURATION_ACTIONS
from ..config import RelayConfig
from ..domain import InvalidCommand, Unavailable, parse_duration
from ..ports import AgentGateway, Clock
from ..presentation import COMMANDS, LABELS, status_text
from .http import HTTPClient, RemoteError
from .sqlite import Repository

LOG = logging.getLogger(__name__)


class Relay:
    def __init__(
        self,
        config: RelayConfig,
        token: str,
        gateway: AgentGateway,
        repository: Repository,
        clock: Clock,
        client: HTTPClient | None = None,
    ) -> None:
        self.config, self.gateway, self.repository, self.clock = config, gateway, repository, clock
        self.token = token
        self.identity = hashlib.sha256(token.encode()).hexdigest()[:16]
        self.client = client or HTTPClient(timeout=25)
        self.pruned_day: str | None = None

    def telegram(self, method: str, payload: dict) -> dict:
        response = self.client.request(
            f"https://api.telegram.org/bot{self.token}/{method}", method="POST", payload=payload
        )
        if response.get("ok") is not True:
            if response.get("error_code") == 429:
                raise Unavailable("Telegram rate limited this request")
            raise Unavailable("Telegram did not accept the request")
        return response

    def authorized(self, chat: object, sender: object) -> bool:
        if str(chat) != self.config.parentGroupChatId or not isinstance(sender, int):
            return False
        response = self.telegram("getChatMember", {"chat_id": chat, "user_id": sender})
        return response.get("result", {}).get("status") in {"administrator", "creator"}

    def child_name(self, value: str) -> str:
        for child in self.config.children:
            if child.casefold() == value.casefold():
                return child
        raise InvalidCommand("Unknown child")

    def send(self, text: str, keyboard: list | None = None, *, reply: bool = False) -> dict:
        payload: dict[str, Any] = {"chat_id": self.config.parentGroupChatId, "text": text[:4000]}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        elif reply:
            payload["reply_markup"] = {"force_reply": True}
        return self.telegram("sendMessage", payload)

    def button(
        self, label: str, child: str, action: str, body: dict | None = None, *, prompt: bool = False
    ) -> dict:
        identifier = uuid4().hex
        self.repository.relay_set(
            "button:" + identifier,
            {
                "child": child,
                "action": action,
                "body": body or {},
                "prompt": prompt,
                "expires": (self.clock.now() + timedelta(hours=24)).isoformat(),
            },
        )
        return {"text": label, "callback_data": identifier}

    def keyboard(self, child: str, status: dict) -> list:
        rows = [
            [
                self.button("Add time", child, "add-time", prompt=True),
                self.button("Set today's limit", child, "set-limit", prompt=True),
            ],
            [
                self.button("Allow until…", child, "allow-until", prompt=True),
                self.button("Allow a session…", child, "grant-session", prompt=True),
            ],
            [
                self.button(
                    "Unlimited budget today", child, "confirm", {"action": "unlimited-today"}
                ),
                self.button("Allow rest of day", child, "confirm", {"action": "allow-today"}),
            ],
            [
                self.button("Restore defaults", child, "restore-today"),
                self.button("Pause today", child, "block", {"today": True}),
            ],
            [self.button("Unblock", child, "unblock"), self.button("Refresh", child, "status")],
            [
                self.button("History", child, "history"),
                self.button(
                    "Unmute requests" if status.get("muted") else "Mute requests",
                    child,
                    "unmute" if status.get("muted") else "mute",
                ),
            ],
        ]
        if status.get("pendingRequest"):
            request = status["pendingRequest"]
            rows += [
                [
                    self.button(
                        f"Approve {minutes}m",
                        child,
                        "decide-request",
                        {
                            "requestId": request["id"],
                            "decision": "grant-session",
                            "minutes": minutes,
                        },
                    )
                    for minutes in dict.fromkeys((request.get("minutes") or 15, 5, 15, 30))
                ],
                [
                    self.button(
                        "Custom approval…",
                        child,
                        "decide-request",
                        {"requestId": request["id"], "decision": "grant-session"},
                        prompt=True,
                    ),
                    self.button(
                        "Deny request",
                        child,
                        "decide-request",
                        {"requestId": request["id"], "decision": "deny"},
                    ),
                ],
                [
                    self.button(
                        "Approve rest of day…",
                        child,
                        "confirm",
                        {
                            "action": "decide-request",
                            "requestId": request["id"],
                            "decision": "allow-today",
                        },
                    ),
                    self.button(
                        "Deny with reason…",
                        child,
                        "decide-request",
                        {
                            "requestId": request["id"],
                            "decision": "deny",
                        },
                        prompt=True,
                    ),
                ],
            ]
        return rows

    def card(self, child: str, status: dict | None = None) -> None:
        status = status or self.gateway.status(child)
        self.send(status_text(child, status), self.keyboard(child, status))

    def history(self, child: str) -> None:
        events = self.gateway.history(child)
        lines = [f"{child}: recent changes"]
        for event in events[:10]:
            lines.append(
                f"{event['at']} · {event['action']} · {event['details'].get('actor', 'legacy')} · {event.get('outcome') or 'recorded'}"
            )
        self.send("\n".join(lines) if events else f"{child}: no recent changes")

    def prompt(self, child: str, action: str, body: dict, sender: int) -> None:
        label = (
            "Deny request"
            if body.get("decision") == "deny"
            else LABELS.get(action, "Approve request")
        )
        result = self.send(
            f"{child}: {label}. "
            + (
                "Reply with a reason."
                if body.get("decision") == "deny"
                else "Reply with HH:MM."
                if action == "allow-until"
                else "Reply with minutes, 25m, or 1h 10m."
            )
            + " Reply /cancel to cancel.",
            reply=True,
        )
        message_id = result["result"]["message_id"]
        self.repository.relay_set(
            f"conversation:{sender}:{message_id}",
            {
                "child": child,
                "action": action,
                "body": body,
                "expires": (self.clock.now() + timedelta(minutes=10)).isoformat(),
            },
        )

    def handle(self, update: dict) -> None:
        callback = update.get("callback_query")
        message = callback.get("message", {}) if callback else update.get("message", {})
        sender = (callback if callback else message).get("from", {}).get("id")
        chat = message.get("chat", {}).get("id")
        if not self.authorized(chat, sender):
            if callback:
                self.telegram(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback["id"],
                        "text": "Not authorized",
                        "show_alert": True,
                    },
                )
            return
        actor = f"telegram:{sender}"
        operation = f"tg:{self.identity}:{update['update_id']}"
        try:
            self.gateway.authorize(actor)
            if callback:
                self.callback(callback, sender, actor)
            else:
                self.message(message, sender, actor, operation)
        except (InvalidCommand, KeyError) as exc:
            self.send(
                str(exc)
                if isinstance(exc, InvalidCommand)
                else "This action is no longer available; refresh /family"
            )

    def callback(self, callback: dict, sender: int, actor: str) -> None:
        data = callback.get("data", "")
        entry = self.repository.relay_get("button:" + data)
        if not isinstance(entry, dict) or entry["expires"] <= self.clock.now().isoformat():
            raise InvalidCommand("This button expired; refresh /family")
        self.telegram("answerCallbackQuery", {"callback_query_id": callback["id"]})
        child, action, body = entry["child"], entry["action"], entry["body"]
        if entry["prompt"]:
            if action in DURATION_ACTIONS:
                self.send(
                    f"{child}: {LABELS.get(action, action)}",
                    [
                        [
                            self.button(f"{minutes}m", child, action, {**body, "minutes": minutes})
                            for minutes in (5, 15, 30, 60)
                        ],
                        [self.button("Custom…", child, "custom", {"action": action, **body})],
                    ],
                )
            else:
                self.prompt(child, action, body, sender)
        elif action == "custom":
            self.prompt(
                child, body["action"], {k: v for k, v in body.items() if k != "action"}, sender
            )
        elif action == "confirm":
            selected = body["action"]
            scope = (
                "budget only; bedtime, homework restrictions and manual blocks still apply"
                if body.get("decision", selected) == "unlimited-today"
                else "budget and schedule; manual blocks still apply"
            )
            self.send(
                f"{child}: remove today's limits for {scope}? Expires at the child's local midnight.",
                [
                    [
                        self.button(
                            "Confirm",
                            child,
                            selected,
                            {k: v for k, v in body.items() if k != "action"},
                        ),
                        self.button("Cancel", child, "status"),
                    ]
                ],
            )
        elif action == "status":
            self.card(child)
        elif action == "history":
            self.history(child)
        else:
            # Stable button identity also serializes two parents clicking one approval.
            result = self.gateway.command(
                child,
                action,
                body,
                f"button:{self.identity}:{data}",
                self.repository.claim("button-actor:" + data, actor),
            )
            self.card(child, result)

    def message(self, message: dict, sender: int, actor: str, operation: str) -> None:
        text = message.get("text", "").strip()
        parts = text.split()
        if not parts:
            return
        if not text.startswith("/"):
            reply_id = message.get("reply_to_message", {}).get("message_id")
            key = f"conversation:{sender}:{reply_id}"
            entry = self.repository.relay_get(key)
            if not isinstance(entry, dict) or entry["expires"] <= self.clock.now().isoformat():
                self.send(
                    "Reply to your custom-time prompt within ten minutes, or start again with /family."
                )
                return
            body = dict(entry["body"])
            if body.get("decision") == "deny":
                body["reason"] = text
            elif entry["action"] == "allow-until":
                body["until"] = text
            else:
                body["minutes"] = parse_duration(
                    text, allow_zero=entry["action"] in {"set-limit", "set-remaining"}
                )
            result = self.gateway.command(entry["child"], entry["action"], body, operation, actor)
            self.card(entry["child"], result)
            self.repository.relay_set(key, None)
            return
        command = parts[0][1:].split("@", 1)[0]
        if command == "cancel":
            reply_id = message.get("reply_to_message", {}).get("message_id")
            self.repository.relay_set(f"conversation:{sender}:{reply_id}", None)
            self.send("Prompt cancelled. Use /family to start again.")
        elif command in {"family", "status", "history"}:
            for child in [self.child_name(parts[1])] if len(parts) == 2 else self.config.children:
                try:
                    if command == "history":
                        self.history(child)
                    else:
                        self.card(child)
                except (Unavailable, OSError):
                    self.send(
                        f"{child}: device unavailable. Its last local policy remains in effect."
                    )
        elif command in COMMANDS:
            action = COMMANDS[command]
            if len(parts) < 2:
                self.send(
                    "Choose a child",
                    [
                        [
                            self.button(
                                child,
                                child,
                                "confirm"
                                if action in {"unlimited-today", "allow-today"}
                                else action,
                                {"action": action}
                                if action in {"unlimited-today", "allow-today"}
                                else {"today": True}
                                if action == "block"
                                else {},
                                prompt=action in DURATION_ACTIONS or action == "allow-until",
                            )
                        ]
                        for child in self.config.children
                    ],
                )
                return
            child = self.child_name(parts[1])
            if action not in DURATION_ACTIONS | {"allow-until"} and len(parts) > (
                3 if action == "block" else 2
            ):
                raise InvalidCommand("Unexpected command arguments; use /help")
            body = {}
            if action in DURATION_ACTIONS:
                if len(parts) < 3:
                    self.prompt(child, action, {}, sender)
                    return
                if action != "extend-access" and any(
                    part.startswith("until=") for part in parts[2:]
                ):
                    raise InvalidCommand("Use /until to change the schedule cutoff")
                duration_parts = [part for part in parts[2:] if not part.startswith("until=")]
                body["minutes"] = parse_duration(
                    " ".join(duration_parts), allow_zero=action in {"set-limit", "set-remaining"}
                )
                for part in parts[2:]:
                    if part.startswith("until=") and action == "extend-access":
                        body["until"] = part.split("=", 1)[1]
            elif action == "allow-until":
                if len(parts) != 3:
                    self.prompt(child, action, {}, sender)
                    return
                body["until"] = parts[2]
            elif action == "block":
                if len(parts) == 2 or parts[2] == "today":
                    body["today"] = True
                else:
                    try:
                        body["days"] = int(parts[2].removesuffix("d"))
                    except ValueError as exc:
                        raise InvalidCommand("Use today or a number of days from 1 to 31") from exc
            result = self.gateway.command(child, action, body, operation, actor)
            self.card(child, result)
        else:
            self.send(
                "/family · /status [child] · /history child\n/addtime child 25m · /setlimit child 90m\n/setremaining child 25m\n"
                "/session child 25m · /until child 19:30\n/unlimited child (budget only)\n"
                "/allday child (budget and schedule)\n/restore child · /block child today · /unblock child\n"
                "/deducttime child 5m · /mute child · /unmute child\nCustom input: reply directly to the prompt; /cancel cancels it."
            )

    def poll_once(self) -> None:
        if self.pruned_day != self.clock.now().date().isoformat():
            self.repository.prune_relay(self.clock.now())
            self.pruned_day = self.clock.now().date().isoformat()
        state = self.repository.relay_get("transport", {"offset": 0, "pending": []})
        if not isinstance(state, dict):
            raise RuntimeError("Invalid relay transport state")
        # Acknowledge durable receipt, not an uncommitted remote side effect. Failed
        # deliveries stay in the inbox while other parents/children can progress.
        if len(state["pending"]) < 1000:
            response = self.telegram(
                "getUpdates",
                {
                    "offset": state["offset"],
                    "timeout": 20,
                    "allowed_updates": ["message", "callback_query"],
                },
            )
            existing = {item["update_id"] for item in state["pending"]}
            for update in response["result"]:
                if update["update_id"] not in existing:
                    update["_receivedAt"] = self.clock.now().timestamp()
                    state["pending"].append(update)
                state["offset"] = max(state["offset"], update["update_id"] + 1)
            self.repository.relay_set("transport", state)
        for update in list(state["pending"]):
            try:
                received = update.get("_receivedAt", self.clock.now().timestamp())
                sent = update.get("message", {}).get("date", received)
                if self.clock.now().timestamp() - min(received, sent) > 600:
                    self.send(
                        "An undelivered action expired after ten minutes. Refresh /family and try again."
                    )
                else:
                    self.handle(update)
            except (Unavailable, OSError):
                LOG.warning("Update %s remains queued", update["update_id"])
                continue
            state["pending"].remove(update)
            self.repository.relay_set("transport", state)

    def event(self, event: dict) -> None:
        child = event["data"].get("child")
        if child not in self.config.children:
            return
        if event["kind"] == "request.changed":
            request = event["data"].get("request")
            if request and request["status"] == "pending":
                status = self.gateway.status(child)
                self.send(
                    f"{child} requested more time: {request.get('reason', '')}",
                    self.keyboard(child, status),
                )
        elif event["kind"] in {"command.applied", "command.failed", "command.expired"}:
            self.card(child)

    def run(self, stop: Any) -> None:
        delay = 1
        while not stop.is_set():
            try:
                self.poll_once()
                delay = 1
            except (Unavailable, RemoteError, OSError, KeyError, ValueError):
                LOG.warning("Telegram delivery incomplete; durable inbox retained")
                stop.wait(delay)
                delay = min(delay * 2, 60)
