"""Matrix room interface with encrypted SDK transport and durable command delivery."""

import argparse
import asyncio
import fcntl
import hashlib
import importlib
import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..application import ADJUSTMENTS
from ..clock import SystemClock
from ..config import MatrixConfig, secret
from ..domain import InvalidCommand, Unavailable, parse_duration
from ..presentation import COMMANDS, status_text
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
        self.transport_errors = transport_errors

    def execute(self, text: str, sender: str, operation: str) -> str:
        self.gateway.authorize("matrix:" + sender)
        parts = text.split()
        command = parts[0].removeprefix("!") if parts else "help"
        if command in {"family", "status"}:
            children = [parts[1]] if len(parts) > 1 else self.config.children
            return "\n\n".join(status_text(child, self.gateway.status(child)) for child in children)
        if len(parts) < 2:
            return "!family · !addtime child 25m · !setremaining child 15m · !until child 19:30 · !allday child · !restore child · !approve child REQUEST_ID 15m · !deny child REQUEST_ID reason · !history child"
        child = parts[1]
        if child not in self.config.children:
            raise InvalidCommand("Unknown child")
        if command == "history":
            return (
                "\n".join(
                    f"{e['at']} · {e['action']} · {e['outcome']}"
                    for e in self.gateway.history(child)
                )
                or "No changes yet"
            )
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
        result = self.gateway.command(child, action, body, operation, "matrix:" + sender)
        return status_text(child, result)

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

    async def send(self, text: str, identity: str) -> None:
        self.verify_devices()
        room = self.client.rooms.get(self.config.roomId)
        if room is None or bool(room.encrypted) != self.config.encrypted:
            raise Unavailable("Matrix room encryption does not match configuration")
        try:
            result = await self.client.room_send(
                self.config.roomId,
                "m.room.message",
                {"msgtype": "m.notice", "body": text[:12000]},
                tx_id=hashlib.sha256(identity.encode()).hexdigest(),
            )
        except self.transport_errors as exc:
            raise Unavailable("Matrix delivery unavailable; message retained for retry") from exc
        if not getattr(result, "event_id", None):
            raise Unavailable("Matrix did not accept the message")

    async def receive(self, room: Any, event: Any) -> None:
        if (
            room.room_id != self.config.roomId
            or event.sender == self.config.userId
            or not event.body.startswith("!")
        ):
            return
        if self.config.encrypted and not getattr(event, "decrypted", False):
            return
        if self.repository.relay_get("matrix-done:" + event.event_id):
            return
        if len(self.repository.relay_items("matrix-inbox:")) >= 1000:
            raise Unavailable("Matrix inbox is full")
        self.repository.relay_set(
            "matrix-inbox:" + event.event_id,
            {
                "sender": event.sender,
                "text": event.body,
                "received": event.server_timestamp / 1000,
            },
        )

    async def drain(self) -> None:
        for key, message in self.repository.relay_items("matrix-inbox:"):
            identity = key.removeprefix("matrix-inbox:")
            try:
                if time.time() - message["received"] > 600:
                    text = "This command expired before delivery. Check !family before retrying."
                else:
                    try:
                        text = await asyncio.to_thread(
                            self.execute,
                            message["text"],
                            message["sender"],
                            "matrix:" + hashlib.sha256(identity.encode()).hexdigest(),
                        )
                    except InvalidCommand as exc:
                        text = str(exc)
                await self.send(text, "reply:" + identity)
            except (Unavailable, OSError):
                continue
            self.repository.relay_set(
                "matrix-done:" + identity, {"expires": self.clock.now().isoformat()}
            )
            self.repository.relay_set(key, None)

    async def notification(self, event: dict) -> None:
        child = event["data"].get("child")
        if child not in self.config.children:
            return
        request = event["data"].get("request")
        if event["kind"] == "request.changed" and request and request["status"] == "pending":
            text = f"{child} requests {request.get('minutes') or 15} minutes: {request.get('reason', '')}\n!approve {child} {request['id']} 15m\n!deny {child} {request['id']} reason"
        elif event["kind"] in {"command.applied", "command.failed", "command.expired"}:
            text = status_text(child, await asyncio.to_thread(self.gateway.status, child))
        else:
            return
        await self.send(text, "event:" + str(event["id"]))


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
