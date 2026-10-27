"""Matrix room interface with encrypted SDK transport and durable command delivery."""

import argparse
import asyncio
import fcntl
import hashlib
import importlib
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..application import ADJUSTMENTS
from ..chat import Chat
from ..clock import SystemClock
from ..config import MatrixConfig, secret
from ..domain import InvalidCommand, Unavailable, parse_duration
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
from .client import Gateway
from .events import consume_events
from .sqlite import Repository

LOG = logging.getLogger(__name__)


class Matrix:
    def __init__(
        self,
        config: MatrixConfig,
        gateway: Gateway,
        repository: Repository,
        client: Any,
        transport_errors: tuple[type[Exception], ...] = (OSError,),
    ):
        self.config, self.gateway, self.repository, self.client = (
            config,
            gateway,
            repository,
            client,
        )
        self.clock = SystemClock("UTC")
        self.chat = Chat("matrix", gateway, repository, self.clock)
        self.transport_errors = transport_errors
        self.last_health = -3600.0
        self.delivery_lock = asyncio.Lock()
        self.pruned_day: str | None = None

    def execute(self, text: str, sender: str, operation: str) -> str:
        parts = text.split()
        command = parts[0].removeprefix("!") if parts else "help"
        if command == "link":
            if len(parts) != 2:
                raise InvalidCommand("Use the link command shown in My account")
            self.gateway.claim_link(parts[1], sender)
            return tr(
                self.gateway.preferences()["defaultLanguage"],
                "Return to My account and confirm the messaging account.",
            )
        actor = "matrix:" + sender
        self.gateway.authorize(actor)
        language = self.chat.language(actor)
        if command in {"language", "roomlanguage"}:
            if len(parts) != 2:
                raise InvalidCommand("!language en|de|default · !roomlanguage en|de|default")
            return self.chat.change_language(
                actor, "me" if command == "language" else "chat", parts[1]
            )
        if command == "confirm":
            if len(parts) != 2:
                raise InvalidCommand("!confirm CONFIRMATION")
            text = self.chat.confirm(parts[1], actor)
            record = self.chat.record("chat-confirm:" + parts[1])
            return text + (
                "\n!confirm " + parts[1] if record.get("pending") else "\n\n" + self.menu(language)
            )
        if command == "family":
            return self.menu(language)
        if command == "more":
            return "\n".join(
                tr(language, label) + ": " + example
                for label, example in (
                    ("Status", "!status"),
                    ("Schedules", "!status child"),
                    ("Deduct time", "!deducttime child 5m"),
                    ("Pause today", "!block child"),
                    ("Unblock", "!unblock child"),
                    ("Unlimited budget today", "!unlimited child"),
                    ("Allow rest of day", "!allday child"),
                    ("Restore defaults", "!restore child"),
                    ("History", "!history child"),
                    ("My language", "!language en|de|default"),
                    ("Group language", "!roomlanguage en|de|default"),
                )
            )
        if command == "status":
            children = [parts[1]] if len(parts) > 1 else self.config.children
            return "\n\n".join(
                status_text(child, self.gateway.status(child), language, self.clock.now())
                for child in children
            )
        if len(parts) < 2:
            return self.menu(language)
        selected = (
            self.config.children if parts[1] == "all" else list(dict.fromkeys(parts[1].split(",")))
        )
        if any(child not in self.config.children for child in selected):
            raise InvalidCommand("Unknown child")
        child = selected[0]
        if len(selected) > 1 and command in {"approve", "deny", "history"}:
            raise InvalidCommand("Choose one child for this action")
        if command == "history":
            return "\n".join(
                f"{timestamp(e['at'], language, self.gateway.status(child).get('timezone', 'UTC'), self.clock.now())} · {tr(language, LABELS.get(e['action'], e['action']))} · {tr(language, e['outcome'] or 'queued')}"
                for e in self.gateway.history(child)
            ) or tr(language, "No recent changes")
        body: dict = {}
        if command in {"approve", "deny"}:
            if len(parts) < 3:
                raise InvalidCommand("A request ID is required")
            action = "decide-request"
            body = {
                "requestId": parts[2],
                "decision": "grant-session" if command == "approve" else "deny",
            }
            if command == "approve":
                body["minutes"] = parse_duration(" ".join(parts[3:]))
            else:
                body["reason"] = " ".join(parts[3:])
        else:
            action = COMMANDS.get(command, "")
            if not action:
                raise InvalidCommand("Unknown command; use !family")
            if action in ADJUSTMENTS:
                body["minutes"] = parse_duration(
                    " ".join(parts[2:]), allow_zero=action in {"set-limit", "set-remaining"}
                )
            elif action == "allow-until":
                if len(parts) != 3:
                    raise InvalidCommand("Use !until child HH:MM")
                body["until"] = parts[2]
            elif action == "block":
                value = parts[2] if len(parts) > 2 else "today"
                if value == "today":
                    body["today"] = True
                else:
                    try:
                        body["days"] = int(value.removesuffix("d"))
                    except ValueError as exc:
                        raise InvalidCommand("Use today or a number of days") from exc
            elif len(parts) != 2:
                raise InvalidCommand("Unexpected arguments")
        if len(selected) > 1 or action in {"unlimited-today", "allow-today"}:
            identifier, text = self.chat.confirmation(selected, action, body, actor)
            return text + f"\n!confirm {identifier}"
        result = self.gateway.command(child, action, body, operation, actor)
        self.repository.relay_set(
            "matrix-result:" + operation,
            {
                "operationId": result.get("operation", {}).get("operationId"),
                "requestId": body.get("requestId"),
                "language": language,
                "expires": (self.clock.now() + timedelta(days=1)).isoformat(),
            },
        )
        return self.chat.receipt(child, result, action, body, actor)

    def menu(self, language: str) -> str:
        names = ", ".join(
            child_label(child, self.gateway.status(child)) + f" ({child})"
            for child in self.config.children
        )
        return (
            tr(language, "What would you like to do?")
            + "\n"
            + names
            + "\n"
            + "\n".join(
                tr(language, label) + ": " + command
                for label, command in (
                    ("Add time", "!addtime child|child,child|all 5|15|30|60m"),
                    ("Extend allowed hours", "!extendaccess child|all 15m"),
                    ("More", "!more"),
                )
            )
        )

    async def health(self, response: Any) -> None:
        """Report real sync outcomes; a running process alone is not a healthy adapter."""
        room = self.client.rooms.get(self.config.roomId)
        connected = (
            hasattr(response, "next_batch")
            and room is not None
            and bool(room.encrypted) == self.config.encrypted
        )
        if connected and self.clock.monotonic() - self.last_health < 30:
            return
        try:
            await asyncio.to_thread(
                self.gateway.integration_health,
                "connected" if connected else "problem",
                self.config.userId,
                self.config.roomId,
            )
            self.last_health = self.clock.monotonic()
        except (Unavailable, InvalidCommand, OSError):
            LOG.warning("Matrix health could not reach the backend")

    def verify_devices(self) -> None:
        for user, devices in self.config.trustedDevices.items():
            for device_id, fingerprint in devices.items():
                device = self.client.device_store[user].get(device_id)
                if device is not None:
                    if device.ed25519 != fingerprint:
                        raise InvalidCommand(
                            "Matrix device fingerprint changed; review trust configuration"
                        )
                    self.client.verify_device(device)

    async def send(self, text: str, identity: str, replace: str | None = None) -> str:
        self.verify_devices()
        room = self.client.rooms.get(self.config.roomId)
        if room is None or bool(room.encrypted) != self.config.encrypted:
            raise Unavailable("Matrix room encryption does not match configuration")
        content: dict = {"msgtype": "m.notice", "body": text[:12000]}
        if replace:
            content = {
                **content,
                "body": "* " + text[:11998],
                "m.new_content": content,
                "m.relates_to": {"rel_type": "m.replace", "event_id": replace},
            }
        try:
            result = await self.client.room_send(
                self.config.roomId,
                "m.room.message",
                content,
                tx_id=hashlib.sha256(identity.encode()).hexdigest(),
            )
        except self.transport_errors as exc:
            raise Unavailable("Matrix delivery unavailable; message retained for retry") from exc
        if not getattr(result, "event_id", None):
            raise Unavailable("Matrix did not accept the message")
        return result.event_id

    async def receive(self, room: Any, event: Any) -> None:
        if room.room_id != self.config.roomId or event.sender == self.config.userId:
            return
        content = getattr(event, "source", {}).get("content", {})
        reply = content.get("m.relates_to", {}).get("m.in_reply_to", {}).get("event_id")
        text = (
            event.body.split("\n\n", 1)[-1] if reply and event.body.startswith(">") else event.body
        )
        if not text.startswith("!"):
            return
        target = self.chat.record("matrix-reply:" + reply) if reply else None
        parts = text.split()
        if target and parts[0] in {"!approve", "!deny"}:
            text = f"{parts[0]} {target['child']} {target['requestId']} " + " ".join(parts[1:])
        if self.config.encrypted and not getattr(event, "decrypted", False):
            return
        if self.chat.record("matrix-done:" + event.event_id):
            return
        if len(self.repository.relay_items("matrix-inbox:")) >= 1000:
            raise Unavailable("Matrix inbox is full")
        self.repository.relay_set(
            "matrix-inbox:" + event.event_id,
            {
                "sender": event.sender,
                "text": text,
                "received": event.server_timestamp / 1000,
            },
        )

    async def drain(self) -> None:
        async with self.delivery_lock:
            await self.deliver_commands()

    async def deliver_commands(self) -> None:
        if self.pruned_day != self.clock.now().date().isoformat():
            self.repository.prune_relay(self.clock.now())
            self.pruned_day = self.clock.now().date().isoformat()
        for key, message in self.repository.relay_items("matrix-inbox:"):
            identity = key.removeprefix("matrix-inbox:")
            try:
                if time.time() - message["received"] > 600:
                    text = tr(
                        await asyncio.to_thread(
                            self.chat.response_language, "matrix:" + message["sender"]
                        ),
                        "This command expired before delivery. Check !family before retrying.",
                    )
                else:
                    try:
                        text = await asyncio.to_thread(
                            self.execute,
                            message["text"],
                            message["sender"],
                            "matrix:" + hashlib.sha256(identity.encode()).hexdigest(),
                        )
                    except InvalidCommand as exc:
                        text = error_text(
                            await asyncio.to_thread(
                                self.chat.response_language, "matrix:" + message["sender"]
                            ),
                            str(exc),
                        )
                operation = "matrix:" + hashlib.sha256(identity.encode()).hexdigest()
                result = self.chat.record("matrix-result:" + operation) or {}
                saved = (
                    self.chat.record("matrix-request:" + result["requestId"])
                    if result.get("requestId")
                    else None
                )
                receipt = (
                    self.chat.record("matrix-receipt:" + result["operationId"])
                    if result.get("operationId")
                    else None
                )
                saved = saved or receipt or {}
                if result.get("operationId"):
                    current = await asyncio.to_thread(self.gateway.operation, result["operationId"])
                    status = await asyncio.to_thread(self.gateway.status, current["child"])
                    text = receipt_text(current["child"], status, current, result["language"])
                sent = await self.send(text, "reply:" + identity, saved.get("messageId"))
                if result.get("operationId"):
                    self.repository.relay_set(
                        "matrix-receipt:" + result["operationId"],
                        {
                            "messageId": saved.get("messageId") or sent,
                            "language": result["language"],
                            "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
                        },
                    )
            except (Unavailable, OSError):
                continue
            self.repository.relay_set(
                "matrix-done:" + identity,
                {"expires": (self.clock.now() + timedelta(days=7)).isoformat()},
            )
            self.repository.relay_set(key, None)

    async def notification(self, event: dict) -> None:
        async with self.delivery_lock:
            await self.deliver_event(event)

    async def deliver_event(self, event: dict) -> None:
        child = event["data"].get("child")
        if child not in self.config.children:
            return
        language = await asyncio.to_thread(self.chat.language)
        status = await asyncio.to_thread(self.gateway.status, child)
        request = event["data"].get("request")
        operation_id = event["data"].get("operationId")
        key = None
        if event["kind"] == "request.changed" and request:
            current = status.get("request")
            if current and current["id"] == request["id"]:
                request = current
            elif current and request["status"] == "pending":
                return
            key = "matrix-request:" + request["id"]
            saved = self.chat.record(key) or {}
            if not saved and request["status"] != "pending":
                return
            text = request_text(child, status, request, language)
            if operation_id:
                text = receipt_text(
                    child,
                    status,
                    await asyncio.to_thread(self.gateway.operation, operation_id),
                    language,
                )
            if request["status"] == "pending":
                text += (
                    "\n"
                    + tr(language, "Choose minutes")
                    + ": 5, 15, 30, 60 · "
                    + tr(language, "Custom…")
                    + f"\n!approve {child} {request['id']} 15m\n!deny {child} {request['id']}"
                )
                text += "\n" + tr(
                    language, "Reply to this message with !approve 15m or !deny reason."
                )
        elif event["kind"] in {"command.applied", "command.failed", "command.expired"}:
            key = "matrix-receipt:" + operation_id
            saved = self.chat.record(key) or {}
            language = saved.get("language", language)
            text = receipt_text(
                child,
                status,
                await asyncio.to_thread(self.gateway.operation, operation_id),
                language,
            )
        else:
            return
        sent = await self.send(text, "event:" + str(event["id"]), saved.get("messageId"))
        record = {
            "messageId": saved.get("messageId") or sent,
            "expires": (self.clock.now() + timedelta(days=7)).isoformat(),
        }
        self.repository.relay_set(key, record)
        if request:
            self.repository.relay_set(
                "matrix-reply:" + record["messageId"],
                {"child": child, "requestId": request["id"], "expires": record["expires"]},
            )
        if operation_id:
            self.repository.relay_set("matrix-receipt:" + operation_id, record)


async def run(config: MatrixConfig, repository: Repository) -> None:
    nio = importlib.import_module("nio")
    client = nio.AsyncClient(
        config.homeserver,
        config.userId,
        device_id=config.deviceId,
        store_path=config.stateDir,
        config=nio.AsyncClientConfig(encryption_enabled=config.encrypted, store_sync_tokens=True),
    )
    try:
        client.restore_login(config.userId, config.deviceId, secret(config.tokenFile))
        joined = await client.join(config.roomId)
        if not getattr(joined, "room_id", None):
            raise Unavailable("Matrix bot could not join its configured room")
        interface = Matrix(
            config,
            Gateway(config.backend),
            repository,
            client,
            (
                nio.exceptions.LocalProtocolError,
                importlib.import_module("aiohttp").ClientError,
                OSError,
            ),
        )
        client.add_event_callback(interface.receive, nio.RoomMessageText)
        client.add_response_callback(interface.health, (nio.SyncResponse, nio.SyncError))
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def deliver(event: dict) -> None:
            pending = asyncio.run_coroutine_threadsafe(interface.notification(event), loop)
            try:
                pending.result(timeout=30)
            except TimeoutError:
                pending.cancel()
                raise

        worker = threading.Thread(
            target=consume_events,
            args=(interface.gateway.client, repository, deliver, stop),
            daemon=True,
        )
        worker.start()
        sync = asyncio.create_task(client.sync_forever(timeout=10000, full_state=True))
        try:
            while not sync.done():
                await interface.drain()
                await asyncio.sleep(1)
            await sync
        finally:
            stop.set()
            sync.cancel()
            await asyncio.gather(sync, return_exceptions=True)
            await asyncio.to_thread(worker.join, 35)
    finally:
        await client.close()
        if client.store is not None:
            client.store.database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    config = MatrixConfig.model_validate_json(Path(args.config).read_text())
    if args.check_config:
        return
    directory = Path(config.stateDir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "matrix.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repository = Repository(str(directory / "interface.sqlite"))
        try:
            asyncio.run(run(config, repository))
        finally:
            repository.close()
