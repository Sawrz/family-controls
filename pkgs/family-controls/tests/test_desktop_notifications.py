"""Verify our notification adapter against a real private session D-Bus."""

import asyncio
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("dbus") is None, reason="Packaged desktop runtime includes D-Bus"
)

SERVICE = r"""
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
import json
import sys
DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName("org.freedesktop.Notifications", bus)
class Notifications(dbus.service.Object):
    @dbus.service.method("org.freedesktop.Notifications", in_signature="susssasa{sv}i", out_signature="u")
    def Notify(self, app, previous, icon, title, body, actions, hints, timeout):
        assert isinstance(hints["urgency"], dbus.Byte)
        with open(sys.argv[1], "a") as output:
            output.write(json.dumps({"title": str(title), "actions": list(actions), "urgency": int(hints["urgency"])}) + "\n")
        return 7
    @dbus.service.signal("org.freedesktop.Notifications", signature="us")
    def ActionInvoked(self, identifier, action):
        pass
    @dbus.service.method("org.freedesktop.Notifications", in_signature="", out_signature="")
    def Activate(self):
        self.ActionInvoked(7, "request")
service = Notifications(bus, "/org/freedesktop/Notifications")
print("ready", flush=True)
GLib.MainLoop().run()
"""


def test_native_warning_thresholds_and_request_action(tmp_path, monkeypatch):
    from family_controls.adapters.desktop_notifications import DesktopNotifications
    from family_controls.notifications import Warnings

    bus_config = tmp_path / "session.conf"
    bus_config.write_text(
        "<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen>"
        '<auth>EXTERNAL</auth><policy context="default"><allow own="*"/>'
        '<allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>'
    )
    result = subprocess.run(
        [
            "dbus-daemon",
            "--config-file=" + str(bus_config),
            "--fork",
            "--print-address",
            "--print-pid",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    address, pid = result.stdout.splitlines()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    messages = tmp_path / "notifications.jsonl"
    process = subprocess.Popen(
        [sys.executable, "-c", SERVICE, str(messages)], stdout=subprocess.PIPE, text=True
    )
    notifier = None
    try:
        assert process.stdout.readline().strip() == "ready"
        activated = threading.Event()
        notifier = DesktopNotifications(activated.set)
        warnings = Warnings()
        for remaining in (400, 299, 298, 59, 58):
            warning = warnings.observe({"remainingCountdownSeconds": remaining})
            if warning:
                notifier.notify(warning)
        delivered = [json.loads(line) for line in messages.read_text().splitlines()]
        assert [message["title"] for message in delivered] == [
            "5 minutes remaining",
            "1 minute remaining",
        ]
        assert delivered[-1]["urgency"] == 2
        assert delivered[0]["actions"] == ["request", "Request more time"]
        asyncio.run_coroutine_threadsafe(notifier.interface.call_activate(), notifier.loop).result(
            timeout=3
        )
        assert activated.wait(3)
    finally:
        if notifier:
            notifier.close()
        process.terminate()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        os.kill(int(pid), signal.SIGTERM)
