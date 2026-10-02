#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
from typing import Any

from common import http_request, load_json


CHILD_AGENT_TIMEOUT_SECONDS = 5
CHILD_AGENT_NETWORK_EXCEPTIONS = (urllib.error.URLError, TimeoutError, OSError)
CHILD_UNREACHABLE_STATUS_KEY = "_relayChildUnreachable"


class ChildAgentUnavailable(RuntimeError):
    def __init__(self, child: str, summary: str) -> None:
        self.child = child
        self.summary = summary
        display_child = child[:1].upper() + child[1:] if child else child
        super().__init__(f"{display_child} agent is unreachable: {summary}")


class Relay:
    PRESET_MINUTES = (5, 15, 30, 60)

    def __init__(self, config: dict[str, Any], bot_token: str) -> None:
        self.config = config
        self.bot_token = bot_token.strip()
        self.offset = 0
        self.pending_messages: dict[str, dict[str, Any]] = {}

    def child_url(self, child: str, suffix: str) -> str:
        entry = self.child_entry(child)
        return f"https://{entry['host']}:{entry['port']}{suffix}"

    def child_name(self, child: str) -> str:
        normalized = child.casefold()
        for configured_child in self.config["hostChildren"]:
            if configured_child.casefold() == normalized:
                return configured_child
        raise ValueError(f"unknown child: {child}")

    @staticmethod
    def display_child_name(child: str) -> str:
        if not child:
            return child
        return child[:1].upper() + child[1:]

    def child_entry(self, child: str) -> dict[str, Any]:
        return self.config["hostChildren"][self.child_name(child)]

    def child_headers(self, child: str) -> dict[str, str]:
        entry = self.child_entry(child)
        token_file = entry.get("apiTokenFile")
        if not token_file:
            return {}
        with open(token_file, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def child_error_summary(exc: BaseException) -> str:
        reason = getattr(exc, "reason", None)
        if reason is not None:
            return str(reason)
        return str(exc)

    @classmethod
    def child_unreachable_status(cls, child: str, exc: BaseException) -> dict[str, Any]:
        return {
            CHILD_UNREACHABLE_STATUS_KEY: True,
            "status": "agent_unreachable",
            "connectionError": cls.child_error_summary(exc),
        }

    @staticmethod
    def is_child_unreachable_status(status: dict[str, Any]) -> bool:
        return bool(status.get(CHILD_UNREACHABLE_STATUS_KEY))

    def child_request(
        self,
        child: str,
        suffix: str,
        *,
        method: str = "GET",
        json_payload: dict[str, Any] | None = None,
        unreachable_status: bool = False,
    ) -> dict[str, Any]:
        child = self.child_name(child)
        url = self.child_url(child, suffix)
        headers = self.child_headers(child)
        try:
            return http_request(
                url,
                method=method,
                json_payload=json_payload,
                headers=headers,
                timeout=CHILD_AGENT_TIMEOUT_SECONDS,
                verify=False,
            )
        except urllib.error.HTTPError:
            raise
        except CHILD_AGENT_NETWORK_EXCEPTIONS as exc:
            if unreachable_status:
                return self.child_unreachable_status(child, exc)
            raise ChildAgentUnavailable(child, self.child_error_summary(exc)) from exc

    def child_status(self, child: str) -> dict[str, Any]:
        entry = self.child_entry(child)
        return self.child_request(
            child,
            f"/v1/users/{entry['user']}/status",
            unreachable_status=True,
        )

    def child_post(self, child: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = self.child_entry(child)
        return self.child_request(
            child,
            f"/v1/users/{entry['user']}/{action}",
            method="POST",
            json_payload=payload,
        )

    def telegram(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        return http_request(
            f"https://api.telegram.org/bot{self.bot_token}/{method}",
            method="POST",
            json_payload=payload,
            verify=True,
        )

    def sync_bot_commands(self) -> None:
        commands = [
            {"command": "help", "description": "Show available commands"},
            {"command": "status", "description": "Show child status"},
            {"command": "addtime", "description": "Add playtime"},
            {"command": "deducttime", "description": "Deduct playtime"},
            {"command": "extendaccess", "description": "Add temporary access exception"},
            {"command": "reduceaccess", "description": "Reduce today's access window"},
            {"command": "mute", "description": "Mute time requests"},
            {"command": "unmute", "description": "Unmute time requests"},
            {"command": "block", "description": "Block screen time"},
            {"command": "unblock", "description": "Unblock screen time"},
        ]
        try:
            self.telegram(
                "setMyCommands",
                {
                    "commands": commands,
                    "scope": {
                        "type": "chat_administrators",
                        "chat_id": self.config["parentGroupChatId"],
                    },
                },
            )
        except Exception as exc:
            print(f"warning: failed to sync Telegram bot commands: {exc}", file=sys.stderr)

    def in_parent_chat(self, chat_id: int | str) -> bool:
        return str(chat_id) == str(self.config["parentGroupChatId"])

    def sender_is_admin(self, user_id: int | str) -> bool:
        response = self.telegram(
            "getChatMember",
            {
                "chat_id": self.config["parentGroupChatId"],
                "user_id": user_id,
            },
        )
        status = response.get("result", {}).get("status")
        return status in {"creator", "administrator"}

    @staticmethod
    def md(value: str) -> str:
        special = "\\_*[]()~`>#+-=|{}.!"
        return "".join(f"\\{char}" if char in special else char for char in value)

    @staticmethod
    def access_until(status: dict[str, Any]) -> str:
        summary = status.get("summary", {})
        value = status.get("allowedUntil") or summary.get("allowedUntil")
        if value:
            return str(value)
        allowed_left = status.get("remainingAllowedSeconds")
        if status.get("insideAllowedWindow") and isinstance(allowed_left, int) and allowed_left > 0:
            return (dt.datetime.now().astimezone() + dt.timedelta(seconds=allowed_left)).strftime(
                "%H:%M"
            )
        return "unknown"

    def status_markdown(self, child: str, status: dict[str, Any]) -> str:
        display_child = self.display_child_name(child)
        if self.is_child_unreachable_status(status):
            return "\n".join(
                [
                    f"*{self.md(display_child)}*",
                    "Status: `agent unreachable`",
                    f"Connection error: `{self.md(str(status.get('connectionError', 'unknown')))}`",
                ]
            )

        if status["insideUnmeteredWindow"]:
            window = "homework"
        elif status["insideAllowedWindow"]:
            window = "allowed"
        else:
            window = "outside"

        lines = [
            f"*{self.md(display_child)}*",
            f"Phase: `{self.md(status['phase'])}`",
            f"Window: `{self.md(window)}`",
            f"PlayTime left: `{self.md(status['summary']['playTimeLeft'])}`",
            f"Access until: `{self.md(self.access_until(status))}`",
            f"Daily limit: `{self.md(str(status['effectiveDailyLimitMinutes']))}m`",
        ]

        pending = status.get("pendingRequest")
        if pending is not None and pending.get("requestedAt"):
            lines.append(f"Request: `{self.md(pending['requestedAt'])}`")
        if status.get("muted"):
            lines.append("Requests: `muted`")
        if status.get("blocked"):
            lines.append(f"Blocked until: `{self.md(status['blockedUntilDate'])}`")

        return "\n".join(lines)

    def adjustment_markdown(
        self, child: str, verb: str, minutes: int, status: dict[str, Any]
    ) -> str:
        display_child = self.display_child_name(child)
        return "\n".join(
            [
                f"*{self.md(display_child)}* {self.md(verb)} `{self.md(str(minutes))}m`",
                f"New PlayTime: `{self.md(status['summary']['playTimeLeft'])}`",
                "",
                self.status_markdown(child, status),
            ]
        )

    def all_statuses_markdown(self) -> str:
        blocks = []
        for child in self.config["hostChildren"]:
            blocks.append(self.status_markdown(child, self.child_status(child)))
        return "\n\n".join(blocks)

    @staticmethod
    def command_label(action: str) -> str:
        return {
            "add-time": "/addtime",
            "deduct-time": "/deducttime",
            "extend-access": "/extendaccess",
            "reduce-access": "/reduceaccess",
            "mute": "/mute",
            "unmute": "/unmute",
            "block": "/block",
            "unblock": "/unblock",
        }[action]

    def child_choice_keyboard(self, action: str) -> list[list[dict[str, str]]]:
        rows = []
        for child in self.config["hostChildren"]:
            display_child = self.display_child_name(child)
            if action in {"add-time", "deduct-time", "extend-access", "reduce-access"}:
                callback_data = f"choose-min:{action}:{child}"
            elif action == "block":
                callback_data = f"choose-block:{child}"
            else:
                callback_data = f"quick:{action}:{child}"
            rows.append([{"text": display_child, "callback_data": callback_data}])
        return rows

    def minute_choice_keyboard(self, action: str, child: str) -> list[list[dict[str, str]]]:
        prefix = "+" if action in {"add-time", "extend-access"} else "-"
        return [
            [
                {
                    "text": f"{prefix}{minutes}m",
                    "callback_data": f"quick:{action}:{child}:{minutes}",
                }
                for minutes in self.PRESET_MINUTES
            ]
        ]

    def block_choice_keyboard(self, child: str) -> list[list[dict[str, str]]]:
        return [
            [
                {"text": "Today", "callback_data": f"quick:block:{child}:today"},
                {"text": "3d", "callback_data": f"quick:block:{child}:3"},
                {"text": "7d", "callback_data": f"quick:block:{child}:7"},
            ]
        ]

    @staticmethod
    def help_markdown() -> str:
        return "\n".join(
            [
                "`/help`",
                "`/status`",
                "`/status <child>`",
                "`/addtime <child> <minutes>`",
                "`/deducttime <child> <minutes>`",
                "`/extendaccess <child> <minutes> [until=HH:MM]`",
                "`/reduceaccess <child> <minutes>`",
                "`/mute <child>`",
                "`/unmute <child>`",
                "`/block <child> <today|days|daysd>`",
                "`/unblock <child>`",
            ]
        )

    def status_keyboard(self, child: str) -> list[list[dict[str, str]]]:
        def button(text: str, action: str) -> dict[str, str]:
            return {"text": text, "callback_data": f"{child}:{action}"}

        return [
            [button("Refresh", "status")],
            [button(f"+{minutes}m", f"add-time:{minutes}") for minutes in self.PRESET_MINUTES],
            [button(f"-{minutes}m", f"deduct-time:{minutes}") for minutes in self.PRESET_MINUTES],
        ]

    def pending_request_keyboard(self, child: str) -> list[list[dict[str, str]]]:
        def button(text: str, action: str) -> dict[str, str]:
            return {"text": text, "callback_data": f"{child}:{action}"}

        return [
            [button(f"+{minutes}m", f"add-time:{minutes}") for minutes in self.PRESET_MINUTES],
            [
                button(f"Extend +{minutes}m", f"extend-access:{minutes}")
                for minutes in self.PRESET_MINUTES
            ],
        ]

    def sync_pending_requests(self) -> None:
        for child in self.config["hostChildren"]:
            display_child = self.display_child_name(child)
            status = self.child_status(child)
            if self.is_child_unreachable_status(status):
                print(
                    f"warning: skipping pending-request sync for {display_child}: "
                    f"{status.get('connectionError', 'unknown')}",
                    file=sys.stderr,
                )
                continue
            phase = status.get("phase")
            pending = status.get("pendingRequest")
            tracked = self.pending_messages.get(child)
            if phase == "pending_request" and pending is not None:
                if tracked and tracked.get("requestedAt") == pending.get("requestedAt"):
                    continue
                message = self.telegram(
                    "sendMessage",
                    {
                        "chat_id": self.config["parentGroupChatId"],
                        "text": (
                            f"*{self.md(display_child)}* requested more time\n"
                            f"PlayTime left: `{self.md(status['summary']['playTimeLeft'])}`\n"
                            f"Access until: `{self.md(self.access_until(status))}`"
                        ),
                        "parse_mode": "MarkdownV2",
                        "reply_markup": {"inline_keyboard": self.pending_request_keyboard(child)},
                    },
                )
                self.pending_messages[child] = {
                    "messageId": message["result"]["message_id"],
                    "requestedAt": pending.get("requestedAt"),
                }
            elif tracked and phase != "pending_request":
                self.pending_messages.pop(child, None)

    @staticmethod
    def http_error_message(exc: urllib.error.HTTPError) -> str:
        error_message = exc.reason
        with exc:
            error_body = exc.read()
        if error_body:
            try:
                parsed = json.loads(error_body.decode("utf-8"))
                error_message = parsed.get("error", error_message)
            except Exception:
                pass
        return str(error_message)

    def edit_callback_message(
        self,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "MarkdownV2",
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": self.config["parentGroupChatId"],
            "message_id": message_id,
            "text": text,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.telegram("editMessageText", payload)

    def resolve_quick_callback(self, action: str, message_id: int) -> None:
        tokens = action.split(":")
        kind = tokens[0]
        if kind == "choose-min" and len(tokens) == 3:
            _, action_name, child = tokens
            child = self.child_name(child)
            self.edit_callback_message(
                message_id,
                f"Choose minutes for `{self.md(self.command_label(action_name))} {self.md(child)}`",
                reply_markup={"inline_keyboard": self.minute_choice_keyboard(action_name, child)},
            )
            return

        if kind == "choose-block" and len(tokens) == 2:
            _, child = tokens
            child = self.child_name(child)
            self.edit_callback_message(
                message_id,
                f"Choose block duration for `{self.md(child)}`",
                reply_markup={"inline_keyboard": self.block_choice_keyboard(child)},
            )
            return

        if kind != "quick" or len(tokens) < 3:
            raise ValueError("unknown callback")

        action_name = tokens[1]
        child = self.child_name(tokens[2])
        if (
            action_name in {"add-time", "deduct-time", "extend-access", "reduce-access"}
            and len(tokens) == 4
        ):
            minutes = int(tokens[3])
            status = self.child_post(child, action_name, {"minutes": minutes})
        elif action_name in {"mute", "unmute", "unblock"} and len(tokens) == 3:
            status = self.child_post(child, action_name, {})
        elif action_name == "block" and len(tokens) == 4:
            value = tokens[3]
            payload = {"today": True} if value == "today" else {"days": int(value)}
            status = self.child_post(child, "block", payload)
        else:
            raise ValueError("unknown callback")

        self.edit_callback_message(message_id, self.status_markdown(child, status))

    def resolve_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        user_id = callback.get("from", {}).get("id")
        if not self.in_parent_chat(chat_id) or not self.sender_is_admin(user_id):
            self.telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "Not authorized",
                    "show_alert": True,
                },
            )
            return

        try:
            data = callback["data"]
            message_id = message.get("message_id")
            if not isinstance(message_id, int):
                raise ValueError("missing message")
            if data.startswith(("choose-min:", "choose-block:", "quick:")):
                self.resolve_quick_callback(data, message_id)
                self.telegram("answerCallbackQuery", {"callback_query_id": callback["id"]})
                return

            child, action = data.split(":", 1)
            child = self.child_name(child)
            tracked = self.pending_messages.get(child)
            is_pending_message = tracked is not None and tracked.get("messageId") == message_id

            if action == "status":
                status = self.child_status(child)
                self.edit_callback_message(
                    message_id,
                    self.status_markdown(child, status),
                    reply_markup={"inline_keyboard": self.status_keyboard(child)},
                )
            elif action.startswith("add-time:"):
                minutes = int(action.split(":", 1)[1])
                status = self.child_post(child, "add-time", {"minutes": minutes})
                payload: dict[str, Any] = {
                    "chat_id": self.config["parentGroupChatId"],
                    "message_id": message_id,
                    "text": self.status_markdown(child, status),
                    "parse_mode": "MarkdownV2",
                }
                if is_pending_message:
                    self.pending_messages.pop(child, None)
                else:
                    payload["reply_markup"] = {"inline_keyboard": self.status_keyboard(child)}
                self.telegram("editMessageText", payload)
            elif action.startswith("deduct-time:"):
                minutes = int(action.split(":", 1)[1])
                status = self.child_post(child, "deduct-time", {"minutes": minutes})
                self.edit_callback_message(
                    message_id,
                    self.status_markdown(child, status),
                    reply_markup={"inline_keyboard": self.status_keyboard(child)},
                )
            elif action.startswith("extend-access:"):
                minutes = int(action.split(":", 1)[1])
                status = self.child_post(child, "extend-access", {"minutes": minutes})
                self.edit_callback_message(message_id, self.status_markdown(child, status))
                self.pending_messages.pop(child, None)
            self.telegram("answerCallbackQuery", {"callback_query_id": callback["id"]})
        except urllib.error.HTTPError as exc:
            self.telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": self.http_error_message(exc),
                    "show_alert": True,
                },
            )
        except ChildAgentUnavailable as exc:
            self.telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": str(exc),
                    "show_alert": True,
                },
            )
        except Exception as exc:
            self.telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": str(exc),
                    "show_alert": True,
                },
            )

    def handle_command(self, message: dict[str, Any]) -> None:
        text = message.get("text", "").strip()
        if not text.startswith("/"):
            return
        parts = text.split()
        command = parts[0].split("@", 1)[0]
        chat_id = message["chat"]["id"]
        user_id = message.get("from", {}).get("id")

        if not self.in_parent_chat(chat_id) or not self.sender_is_admin(user_id):
            self.telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Not authorized",
                },
            )
            return

        reply_markup: dict[str, Any] | None = None
        try:
            if command == "/status" and len(parts) == 1:
                reply = self.all_statuses_markdown()
                parse_mode = "MarkdownV2"
            elif command == "/status" and len(parts) == 2:
                child = self.child_name(parts[1])
                status = self.child_status(child)
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
                reply_markup = {"inline_keyboard": self.status_keyboard(child)}
            elif (
                command in {"/addtime", "/deducttime", "/extendaccess", "/reduceaccess"}
                and len(parts) == 1
            ):
                action = {
                    "/addtime": "add-time",
                    "/deducttime": "deduct-time",
                    "/extendaccess": "extend-access",
                    "/reduceaccess": "reduce-access",
                }[command]
                reply = f"Choose child for `{self.md(command)}`"
                parse_mode = "MarkdownV2"
                reply_markup = {"inline_keyboard": self.child_choice_keyboard(action)}
            elif (
                command in {"/addtime", "/deducttime", "/extendaccess", "/reduceaccess"}
                and len(parts) == 2
            ):
                action = {
                    "/addtime": "add-time",
                    "/deducttime": "deduct-time",
                    "/extendaccess": "extend-access",
                    "/reduceaccess": "reduce-access",
                }[command]
                child = self.child_name(parts[1])
                reply = f"Choose minutes for `{self.md(command)} {self.md(child)}`"
                parse_mode = "MarkdownV2"
                reply_markup = {"inline_keyboard": self.minute_choice_keyboard(action, child)}
            elif command == "/addtime" and len(parts) == 3:
                child = self.child_name(parts[1])
                minutes = int(parts[2])
                status = self.child_post(child, "add-time", {"minutes": minutes})
                reply = self.adjustment_markdown(child, "added", minutes, status)
                parse_mode = "MarkdownV2"
            elif command == "/deducttime" and len(parts) == 3:
                child = self.child_name(parts[1])
                minutes = int(parts[2])
                status = self.child_post(child, "deduct-time", {"minutes": minutes})
                reply = self.adjustment_markdown(child, "deducted", minutes, status)
                parse_mode = "MarkdownV2"
            elif command == "/extendaccess" and len(parts) >= 3:
                child = self.child_name(parts[1])
                payload: dict[str, Any] = {"minutes": int(parts[2])}
                for extra in parts[3:]:
                    if extra.startswith("until="):
                        payload["until"] = extra.split("=", 1)[1]
                status = self.child_post(child, "extend-access", payload)
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
            elif command == "/reduceaccess" and len(parts) == 3:
                child = self.child_name(parts[1])
                minutes = int(parts[2])
                status = self.child_post(child, "reduce-access", {"minutes": minutes})
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
            elif command in {"/mute", "/unmute", "/unblock"} and len(parts) == 1:
                action = command.removeprefix("/")
                reply = f"Choose child for `{self.md(command)}`"
                parse_mode = "MarkdownV2"
                reply_markup = {"inline_keyboard": self.child_choice_keyboard(action)}
            elif command == "/mute" and len(parts) == 2:
                child = self.child_name(parts[1])
                status = self.child_post(child, "mute", {})
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
            elif command == "/unmute" and len(parts) == 2:
                child = self.child_name(parts[1])
                status = self.child_post(child, "unmute", {})
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
            elif command == "/block" and len(parts) == 1:
                reply = f"Choose child for `{self.md(command)}`"
                parse_mode = "MarkdownV2"
                reply_markup = {"inline_keyboard": self.child_choice_keyboard("block")}
            elif command == "/block" and len(parts) == 2:
                child = self.child_name(parts[1])
                reply = f"Choose block duration for `{self.md(child)}`"
                parse_mode = "MarkdownV2"
                reply_markup = {"inline_keyboard": self.block_choice_keyboard(child)}
            elif command == "/block" and len(parts) == 3:
                child = self.child_name(parts[1])
                value = parts[2].casefold()
                if value == "today":
                    block_payload = {"today": True}
                else:
                    block_payload = {"days": int(value[:-1] if value.endswith("d") else value)}
                status = self.child_post(child, "block", block_payload)
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
            elif command == "/unblock" and len(parts) == 2:
                child = self.child_name(parts[1])
                status = self.child_post(child, "unblock", {})
                reply = self.status_markdown(child, status)
                parse_mode = "MarkdownV2"
            elif command == "/help":
                reply = self.help_markdown()
                parse_mode = "MarkdownV2"
            else:
                reply = "Unknown command"
                parse_mode = None
        except urllib.error.HTTPError as exc:
            reply = self.http_error_message(exc)
            parse_mode = None
        except ChildAgentUnavailable as exc:
            reply = str(exc)
            parse_mode = None
        except ValueError as exc:
            reply = str(exc)
            parse_mode = None
        except Exception as exc:
            reply = f"command failed: {exc}"
            parse_mode = None

        payload: dict[str, Any] = {"chat_id": chat_id, "text": reply}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.telegram("sendMessage", payload)

    def poll_telegram(self) -> None:
        response = http_request(
            f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={self.offset}&timeout=20",
            verify=True,
        )
        for update in response.get("result", []):
            self.offset = max(self.offset, update["update_id"] + 1)
            if "callback_query" in update:
                self.resolve_callback(update["callback_query"])
            elif "message" in update:
                self.handle_command(update["message"])

    def run(self) -> None:
        self.sync_bot_commands()
        while True:
            try:
                self.sync_pending_requests()
            except Exception as exc:
                print(f"warning: sync_pending_requests failed: {exc}", file=sys.stderr)
            try:
                self.poll_telegram()
            except Exception as exc:
                print(f"warning: poll_telegram failed: {exc}", file=sys.stderr)
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--bot-token-file", required=True)
    args = parser.parse_args()

    config = load_json(args.config)
    with open(args.bot_token_file, "r", encoding="utf-8") as fh:
        bot_token = fh.read().strip()
    Relay(config, bot_token).run()


if __name__ == "__main__":
    main()
