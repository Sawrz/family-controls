"""Freedesktop notifications with a Request more time action on a private D-Bus."""

import asyncio
import importlib
import logging
import threading
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger(__name__)


class DesktopNotifications:
    def __init__(self, show: Callable[[], None]) -> None:
        self.show = show
        self.interface: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop: asyncio.Event | None = None
        self.ids: set[int] = set()
        self.ready = threading.Event()
        self.shutdown = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def run(self) -> None:
        while not self.shutdown.is_set():
            try:
                asyncio.run(self.connect())
            except Exception:
                LOG.warning(
                    "Desktop notifications unavailable; local warning window remains enabled"
                )
            finally:
                self.ready.set()
            self.shutdown.wait(5)

    async def connect(self) -> None:
        dbus = importlib.import_module("dbus_fast.aio")
        self.variant = importlib.import_module("dbus_fast").Variant
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        bus = await asyncio.wait_for(dbus.MessageBus().connect(), 3)
        try:
            name, path = "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
            schema = await asyncio.wait_for(bus.introspect(name, path), 3)
            self.interface = bus.get_proxy_object(name, path, schema).get_interface(name)
            self.interface.on_action_invoked(self.action)
            self.ready.set()
            await self.stop.wait()
        finally:
            self.interface = None
            bus.disconnect()
            await bus.wait_for_disconnect()

    def action(self, identifier: int, action: str) -> None:
        if identifier in self.ids and action == "request":
            self.show()

    async def deliver(self, seconds: int) -> None:
        identifier = await self.interface.call_notify(
            "Family Controls",
            0,
            "appointment-soon",
            f"{seconds // 60} minute{'s' if seconds != 60 else ''} remaining",
            "Save your work or request more time.",
            ["request", "Request more time"],
            {"urgency": self.variant("y", 2 if seconds == 60 else 1)},
            15000,
        )
        self.ids.add(identifier)

    def notify(self, seconds: int) -> None:
        if self.interface is None or self.loop is None:
            return
        pending = asyncio.run_coroutine_threadsafe(self.deliver(seconds), self.loop)
        try:
            pending.result(timeout=5)
        except Exception:
            pending.cancel()
            LOG.warning("Cannot send desktop warning; showing local window")

    def close(self) -> None:
        self.shutdown.set()
        if self.loop and self.stop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(timeout=7)
