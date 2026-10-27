"""Telegram presentation and durable delivery; policy stays behind AgentGateway."""

import hashlib
import logging
import threading
from datetime import timedelta
from typing import Any
from uuid import uuid4

from ..application import ADJUSTMENTS as DURATION_ACTIONS
from ..chat import Chat
from ..config import RelayConfig
from ..domain import InvalidCommand, Unavailable, parse_duration
from ..ports import AgentGateway, Clock
from ..presentation import (
    COMMANDS,
    LABELS,
    child_label,
    error_text,
    receipt_text,
    request_text,
    status_text,
    timestamp,
    tr,
)
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
        self.chat = Chat("telegram", gateway, repository, clock)
        self.event_lock = threading.RLock()
        self.token = token
        self.identity = hashlib.sha256(token.encode()).hexdigest()[:16]
        self.client = client or HTTPClient(timeout=25)
        self.pruned_day: str | None = None
        self.health_checked = -3600.0
        self.bot_name: str | None = None
        self.destination = config.parentGroupChatId

    def telegram(self, method: str, payload: dict) -> dict:
        response = self.client.request(
            f"https://api.telegram.org/bot{self.token}/{method}", method="POST", payload=payload
        )
        if method.startswith("editMessage") and "message is not modified" in response.get(
            "description", ""
        ):
            return response
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
        self,
        label: str,
        child: str,
        action: str,
        body: dict | None = None,
        *,
        prompt: bool = False,
        owner: int | None = None,
    ) -> dict:
        identifier = uuid4().hex
        self.repository.relay_set(
            "button:" + identifier,
            {
                "child": child,
                "action": action,
                "body": body or {},
                "prompt": prompt,
                "owner": owner,
                "expires": (self.clock.now() + timedelta(hours=24)).isoformat(),
            },
        )
        return {"text": label, "callback_data": identifier}

    def keyboard(self, child: str, status: dict) -> list:
        language = self.chat.language()
        request = status.get("pendingRequest")
        if not request:
            return [[self.button(tr(language, "Menu"), "", "menu")]]
        body = {"requestId": request["id"], "decision": "grant-session"}
        return [
            [
                self.button(
                    tr(language, "Approve {minutes} min", minutes=m),
                    child,
                    "decide-request",
                    {**body, "minutes": m},
                )
                for m in (5, 15)
            ],
            [
                self.button(
                    tr(language, "Approve {minutes} min", minutes=m),
                    child,
                    "decide-request",
                    {**body, "minutes": m},
                )
                for m in (30, 60)
            ],
            [
                self.button(tr(language, "Custom…"), child, "decide-request", body, prompt=True),
                self.button(
                    tr(language, "Deny"),
                    child,
                    "decide-request",
                    {"requestId": request["id"], "decision": "deny"},
                ),
            ],
            [
                self.button(
                    tr(language, "Deny with reason…"),
                    child,
                    "decide-request",
                    {"requestId": request["id"], "decision": "deny"},
                    prompt=True,
                )
            ],
        ]

    def render(self, text: str, rows: list, message_id: int | None = None) -> dict:
        if message_id is None:
            return self.send(text, rows)
        return self.telegram(
            "editMessageText",
            {
                "chat_id": self.config.parentGroupChatId,
                "message_id": message_id,
                "text": text[:4000],
                "reply_markup": {"inline_keyboard": rows},
            },
        )

    def menu(
        self,
        sender: int,
        actor: str,
        message_id: int | None = None,
        *,
        more: bool = False,
        notice: str = "",
    ) -> None:
        language = self.chat.language(actor)
        actions = [
            ("Add time", "add-time"),
            ("Extend allowed hours", "extend-access"),
            ("More", "more"),
        ]
        if more:
            actions = [
                ("Status", "status"),
                ("Schedules", "status"),
                ("Deduct time", "deduct-time"),
                ("Set today's limit", "set-limit"),
                ("Set time left", "set-remaining"),
                ("Pause today", "block"),
                ("Unblock", "unblock"),
                ("Unlimited budget today", "unlimited-today"),
                ("Allow rest of day", "allow-today"),
                ("Restore defaults", "restore-today"),
                ("History", "history"),
                ("Language", "languages"),
                ("Back", "menu"),
            ]
        rows = [
            [
                self.button(
                    tr(language, label),
                    "",
                    action if action in {"more", "menu", "languages"} else "select",
                    {"action": action},
                    owner=sender,
                )
            ]
            for label, action in actions
        ]
        self.render(
            (notice + "\n\n" if notice else "") + tr(language, "What would you like to do?"),
            rows,
            message_id,
        )

    def selection(
        self, sender: int, actor: str, action: str, selected: list[str], message_id: int | None
    ) -> None:
        language = self.chat.language(actor)
        rows = [
            [
                self.button(
                    ("✓ " if child in selected else "")
                    + child_label(child, self.gateway.status(child)),
                    "",
                    "select",
                    {
                        "action": action,
                        "selected": [c for c in selected if c != child]
                        if child in selected
                        else [*selected, child],
                    },
                    owner=sender,
                )
            ]
            for child in self.config.children
        ]
        rows += [
            [
                self.button(
                    tr(language, "All children"),
                    "",
                    "select",
                    {"action": action, "selected": self.config.children},
                    owner=sender,
                )
            ],
            [
                self.button(
                    tr(language, "Continue"),
                    "",
                    "selected",
                    {"action": action, "selected": selected},
                    owner=sender,
                ),
                self.button(tr(language, "Back"), "", "menu", owner=sender),
            ],
        ]
        self.render(tr(language, "Choose children"), rows, message_id)

    def amounts(
        self, sender: int, actor: str, action: str, selected: list[str], message_id: int | None
    ) -> None:
        language = self.chat.language(actor)
        rows = [
            [
                self.button(
                    f"{m} min",
                    "",
                    "prepare",
                    {"action": action, "selected": selected, "minutes": m},
                    owner=sender,
                )
                for m in (5, 15, 30, 60)
            ],
            [
                self.button(
                    tr(language, "Custom…"),
                    ",".join(selected),
                    "custom",
                    {"action": action, "bulk": True, "menuId": message_id},
                    owner=sender,
                ),
                self.button(
                    tr(language, "Back"),
                    "",
                    "select",
                    {"action": action, "selected": selected},
                    owner=sender,
                ),
            ],
        ]
        self.render(tr(language, "Choose minutes"), rows, message_id)

    def prepare(
        self,
        selected: list[str],
        action: str,
        body: dict,
        sender: int,
        actor: str,
        message_id: int | None,
    ) -> None:
        identifier, text = self.chat.confirmation(selected, action, body, actor)
        language = self.chat.language(actor)
        self.render(
            text,
            [
                [
                    self.button(
                        tr(language, "Confirm"),
                        "",
                        "commit",
                        {"confirmation": identifier},
                        owner=sender,
                    ),
                    self.button(tr(language, "Cancel"), "", "menu", owner=sender),
                ]
            ],
            message_id,
        )

    def card(self, child: str, status: dict | None = None, actor: str | None = None) -> None:
        status = status or self.gateway.status(child)
        self.send(status_text(child, status, self.chat.language(actor), self.clock.now()))

    def history(self, child: str, actor: str | None = None) -> None:
        language = self.chat.language(actor)
        status = self.gateway.status(child)
        lines = [child_label(child, status)]
        for event in self.gateway.history(child)[:10]:
            lines.append(
                f"{timestamp(event['at'], language, status.get('timezone', 'UTC'), self.clock.now())} · {tr(language, LABELS.get(event['action'], event['action']))} · {tr(language, event.get('outcome') or 'queued')} · {event.get('details', {}).get('actor', '')}"
            )
        self.send("\n".join(lines) if len(lines) > 1 else tr(language, "No recent changes"))

    def prompt(self, child: str, action: str, body: dict, sender: int) -> None:
        language = self.chat.language(f"telegram:{sender}")
        hint = (
            "Reply with a reason. /cancel cancels."
            if body.get("decision") == "deny"
            else "Reply with HH:MM. /cancel cancels."
            if action == "allow-until"
            else "Reply with minutes, 25m, or 1h 10m. /cancel cancels."
        )
        result = self.send(tr(language, hint), reply=True)
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
        parts = message.get("text", "").split()
        if (
            not callback
            and len(parts) == 2
            and parts[0].split("@", 1)[0] in {"/link", "/start"}
            and message.get("chat", {}).get("type") == "private"
            and isinstance(sender, int)
            and chat == sender
        ):
            try:
                profile = message.get("from", {})
                label = (
                    "@" + profile["username"]
                    if profile.get("username")
                    else profile.get("first_name")
                )
                self.gateway.claim_link(parts[1], str(sender), label)
                text = tr(
                    self.gateway.preferences()["defaultLanguage"],
                    "Return to My account and confirm the messaging account.",
                )
            except InvalidCommand:
                text = tr(
                    self.gateway.preferences()["defaultLanguage"],
                    "Link code is invalid or expired. Start again in My account.",
                )
            self.telegram("sendMessage", {"chat_id": chat, "text": text})
            return
        if not self.authorized(chat, sender):
            if callback:
                self.telegram(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback["id"],
                        "text": tr(self.chat.language(), "Not authorized"),
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
                error_text(self.chat.response_language(actor), str(exc))
                if isinstance(exc, InvalidCommand)
                else tr(
                    self.chat.response_language(actor), "This action expired. Open the menu again."
                )
            )

    def callback(self, callback: dict, sender: int, actor: str) -> None:
        data = callback.get("data", "")
        entry = self.chat.record("button:" + data)
        if not entry or entry["expires"] <= self.clock.now().isoformat():
            raise InvalidCommand(
                tr(self.chat.language(actor), "This action expired. Open the menu again.")
            )
        self.telegram("answerCallbackQuery", {"callback_query_id": callback["id"]})
        child, action, body = entry["child"], entry["action"], entry["body"]
        language = self.chat.language(actor)
        message_id = callback.get("message", {}).get("message_id")
        if entry.get("owner") not in (None, sender):
            raise InvalidCommand(tr(language, "This menu belongs to another parent. Open /family."))
        if action in {"menu", "more"}:
            self.menu(sender, actor, message_id, more=action == "more")
        elif action == "select":
            self.selection(sender, actor, body["action"], body.get("selected", []), message_id)
        elif action == "selected":
            selected = body["selected"]
            if not selected:
                raise InvalidCommand(tr(language, "Select at least one child."))
            if body["action"] in {"status", "history"}:
                for selected_child in selected:
                    if body["action"] == "history":
                        self.history(selected_child, actor)
                    else:
                        self.card(selected_child, actor=actor)
                self.menu(sender, actor, message_id)
            elif body["action"] in DURATION_ACTIONS:
                self.amounts(sender, actor, body["action"], selected, message_id)
            else:
                self.prepare(
                    selected,
                    body["action"],
                    {"today": True} if body["action"] == "block" else {},
                    sender,
                    actor,
                    message_id,
                )
        elif action == "prepare":
            self.prepare(
                body["selected"],
                body["action"],
                {"minutes": body["minutes"]},
                sender,
                actor,
                message_id,
            )
        elif action == "commit":
            text = self.chat.confirm(body["confirmation"], actor)
            confirmation = self.chat.record("chat-confirm:" + body["confirmation"])
            if confirmation.get("pending"):
                self.render(
                    text,
                    [
                        [
                            self.button(tr(language, "Confirm"), "", "commit", body, owner=sender),
                            self.button(tr(language, "Cancel"), "", "menu", owner=sender),
                        ]
                    ],
                    message_id,
                )
            else:
                self.menu(sender, actor, message_id, notice=text)
                if message_id is not None:
                    for target in confirmation["children"]:
                        operation_id = f"chat:telegram:{body['confirmation']}:{target}"
                        self.repository.relay_set(
                            "receipt-message:" + operation_id,
                            {
                                "messageId": message_id,
                                "confirmation": body["confirmation"],
                                "sender": sender,
                                "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
                            },
                        )
        elif action == "languages":
            rows = [
                [
                    self.button(
                        tr(language, "My language"),
                        "",
                        "language-menu",
                        {"scope": "me"},
                        owner=sender,
                    )
                ]
            ]
            if self.gateway.authorize(actor).get("role") == "admin":
                rows.append(
                    [
                        self.button(
                            tr(language, "Group language"),
                            "",
                            "language-menu",
                            {"scope": "chat"},
                            owner=sender,
                        )
                    ]
                )
            rows.append([self.button(tr(language, "Back"), "", "menu", owner=sender)])
            self.render(tr(language, "Language"), rows, message_id)
        elif action == "language-menu":
            self.render(
                tr(language, "My language" if body["scope"] == "me" else "Group language"),
                [
                    [
                        self.button(
                            label,
                            "",
                            "language",
                            {"scope": body["scope"], "language": value},
                            owner=sender,
                        )
                    ]
                    for value, label in (
                        ("en", "English"),
                        ("de", "Deutsch"),
                        ("default", tr(language, "Household default")),
                    )
                ],
                message_id,
            )
        elif action == "language":
            text = self.chat.change_language(actor, body["scope"], body["language"])
            self.menu(sender, actor, message_id, notice=text)
        elif action == "requests":
            for selected_child in self.config.children:
                status = self.gateway.status(selected_child)
                if status.get("pendingRequest"):
                    self.event(
                        {
                            "kind": "request.changed",
                            "data": {"child": selected_child, "request": status["pendingRequest"]},
                        }
                    )
        elif entry["prompt"]:
            self.prompt(child, action, body, sender)
        elif action == "custom":
            self.prompt(
                child, body["action"], {k: v for k, v in body.items() if k != "action"}, sender
            )
        else:
            result = self.gateway.command(
                child,
                action,
                body,
                f"button:{self.identity}:{data}",
                self.repository.claim("button-actor:" + data, actor),
            )
            if action == "decide-request":
                self.event(
                    {
                        "kind": "request.changed",
                        "data": {
                            "child": child,
                            "request": result["request"],
                            "operationId": result.get("operation", {}).get("operationId"),
                        },
                    }
                )
            else:
                self.send_receipt(child, result, action, body, actor)

    def message(self, message: dict, sender: int, actor: str, operation: str) -> None:
        text = message.get("text", "").strip()
        parts = text.split()
        if not parts:
            return
        if not text.startswith("/"):
            reply_id = message.get("reply_to_message", {}).get("message_id")
            key = f"conversation:{sender}:{reply_id}"
            entry = self.chat.record(key)
            if not entry or entry["expires"] <= self.clock.now().isoformat():
                self.send(
                    tr(
                        self.chat.language(actor),
                        "Reply to your prompt within ten minutes, or open the menu again.",
                    )
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
            if body.pop("bulk", False):
                menu_id = body.pop("menuId", None)
                self.prepare(
                    entry["child"].split(","), entry["action"], body, sender, actor, menu_id
                )
            else:
                result = self.gateway.command(
                    entry["child"], entry["action"], body, operation, actor
                )
                if entry["action"] != "decide-request":
                    self.send_receipt(entry["child"], result, entry["action"], body, actor)
                else:
                    self.event(
                        {
                            "kind": "request.changed",
                            "data": {
                                "child": entry["child"],
                                "request": result["request"],
                                "operationId": result.get("operation", {}).get("operationId"),
                            },
                        }
                    )
            self.repository.relay_set(key, None)
            return
        command = parts[0][1:].split("@", 1)[0]
        if command == "cancel":
            reply_id = message.get("reply_to_message", {}).get("message_id")
            self.repository.relay_set(f"conversation:{sender}:{reply_id}", None)
            self.send(tr(self.chat.language(actor), "Prompt cancelled."))
        elif command == "family":
            self.menu(sender, actor)
        elif command in {"language", "grouplanguage"}:
            if len(parts) != 2:
                raise InvalidCommand("/language en|de|default · /grouplanguage en|de|default")
            self.send(
                self.chat.change_language(
                    actor, "me" if command == "language" else "chat", parts[1]
                )
            )
        elif command in {"status", "history"}:
            for child in [self.child_name(parts[1])] if len(parts) == 2 else self.config.children:
                try:
                    if command == "history":
                        self.history(child, actor)
                    else:
                        self.card(child, actor=actor)
                except (Unavailable, OSError):
                    self.send(
                        tr(
                            self.chat.response_language(actor),
                            "{name}: device unavailable. Its last local policy remains in effect.",
                            name=child[:1].upper() + child[1:],
                        )
                    )
        elif command in COMMANDS:
            action = COMMANDS[command]
            if len(parts) < 2:
                self.selection(sender, actor, action, [], None)
                return
            selected = (
                self.config.children
                if parts[1] == "all"
                else list(dict.fromkeys(self.child_name(c) for c in parts[1].split(",")))
            )
            child = selected[0]
            if action not in DURATION_ACTIONS | {"allow-until"} and len(parts) > (
                3 if action == "block" else 2
            ):
                raise InvalidCommand("Unexpected command arguments; use /help")
            body = {}
            if action in DURATION_ACTIONS:
                if len(parts) < 3:
                    self.prompt(
                        ",".join(selected),
                        action,
                        {"bulk": True} if len(selected) > 1 else {},
                        sender,
                    )
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
            if len(selected) > 1 or action in {"unlimited-today", "allow-today"}:
                self.prepare(selected, action, body, sender, actor, None)
            else:
                result = self.gateway.command(child, action, body, operation, actor)
                self.send_receipt(child, result, action, body, actor)
        else:
            self.menu(sender, actor, more=True)

    def send_receipt(self, child: str, result: dict, action: str, body: dict, actor: str) -> None:
        operation_id = result.get("operation", {}).get("operationId")
        with self.event_lock:
            if operation_id:
                result = {**result, "operation": self.gateway.operation(operation_id)}
            saved = self.chat.record("receipt-message:" + operation_id) if operation_id else None
            response = self.render(
                self.chat.receipt(child, result, action, body, actor),
                [],
                saved.get("messageId") if saved else None,
            )
            if operation_id:
                self.repository.relay_set(
                    "receipt-message:" + operation_id,
                    {
                        "language": self.chat.language(actor),
                        "messageId": saved.get("messageId")
                        if saved
                        else response["result"]["message_id"],
                        "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
                    },
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
                        tr(
                            self.chat.response_language(
                                "telegram:"
                                + str(
                                    (update.get("callback_query") or update.get("message", {}))
                                    .get("from", {})
                                    .get("id", "")
                                )
                            ),
                            "An undelivered action expired after ten minutes. Refresh /family and try again.",
                        )
                    )
                else:
                    self.handle(update)
            except (Unavailable, OSError):
                LOG.warning("Update %s remains queued", update["update_id"])
                continue
            state["pending"].remove(update)
            self.repository.relay_set("transport", state)

    def event(self, event: dict) -> None:
        with self.event_lock:
            self.deliver_event(event)

    def deliver_event(self, event: dict) -> None:
        child = event["data"].get("child")
        if child not in self.config.children:
            return
        language = self.chat.language()
        status = self.gateway.status(child)
        request = event["data"].get("request")
        operation_id = event["data"].get("operationId")
        if event["kind"] == "request.changed" and request:
            current = status.get("request")
            if current and current["id"] == request["id"]:
                request = current
            key = "request-message:" + request["id"]
            saved = self.chat.record(key) or {}
            if request["status"] == "pending" and current and current["id"] != request["id"]:
                return
            if not saved and request["status"] != "pending":
                return
            pending = request["status"] == "pending"
            rows = (
                self.keyboard(child, {**status, "pendingRequest": request})
                if pending
                else self.resolved_keyboard(language)
            )
            text = request_text(child, status, request, language)
            if operation_id:
                text = receipt_text(child, status, self.gateway.operation(operation_id), language)
            result = self.render(text, rows, saved.get("messageId"))
            message_id = saved.get("messageId") or result["result"]["message_id"]
            self.repository.relay_set(
                key,
                {
                    "messageId": message_id,
                    "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
                },
            )
            if operation_id:
                self.repository.relay_set(
                    "receipt-message:" + operation_id,
                    {
                        "messageId": message_id,
                        "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
                    },
                )
        elif event["kind"] in {"command.applied", "command.failed", "command.expired"}:
            saved = self.chat.record("receipt-message:" + operation_id) or {}
            if saved.get("confirmation"):
                confirmation = self.chat.record("chat-confirm:" + saved["confirmation"])
                if confirmation:
                    actor = f"telegram:{saved['sender']}"
                    lines = [
                        receipt_text(
                            target,
                            self.gateway.status(target),
                            self.gateway.operation(
                                f"chat:telegram:{saved['confirmation']}:{target}"
                            ),
                            self.chat.language(actor),
                        )
                        for target in confirmation["children"]
                    ]
                    self.menu(saved["sender"], actor, saved["messageId"], notice="\n".join(lines))
                    return
            language = saved.get("language", language)
            operation = self.gateway.operation(operation_id)
            text = receipt_text(child, status, operation, language)
            rows = (
                self.resolved_keyboard(language) if operation["action"] == "decide-request" else []
            )
            response = self.render(text, rows, saved.get("messageId"))
            if not saved:
                self.repository.relay_set(
                    "receipt-message:" + operation_id,
                    {
                        "messageId": response["result"]["message_id"],
                        "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
                    },
                )

    def resolved_keyboard(self, language: str) -> list:
        pending = any(
            self.gateway.status(child).get("pendingRequest") for child in self.config.children
        )
        return [
            [
                self.button(
                    tr(language, "Review remaining requests" if pending else "Menu"),
                    "",
                    "requests" if pending else "menu",
                )
            ]
        ]

    def run(self, stop: Any) -> None:
        delay = 1
        while not stop.is_set():
            try:
                if self.clock.monotonic() - self.health_checked >= 60:
                    bot = self.telegram("getMe", {})["result"]
                    chat = self.telegram("getChat", {"chat_id": self.config.parentGroupChatId})[
                        "result"
                    ]
                    self.bot_name = "@" + bot["username"] if bot.get("username") else str(bot["id"])
                    self.destination = (
                        f"{chat.get('title', 'Telegram')} ({self.config.parentGroupChatId})"
                    )
                    self.health_checked = self.clock.monotonic()
                self.poll_once()
                self.gateway.integration_health("connected", self.bot_name, self.destination)
                delay = 1
            except (Unavailable, RemoteError, OSError, KeyError, ValueError):
                LOG.warning("Telegram delivery incomplete; durable inbox retained")
                try:
                    self.gateway.integration_health("problem", self.bot_name, self.destination)
                except (Unavailable, InvalidCommand, OSError):
                    LOG.warning("Telegram health could not reach the backend")
                stop.wait(delay)
                delay = min(delay * 2, 60)
