"""Read session state from logind; Timekpr remains the only session enforcer."""

import importlib
import time

from ..domain import Unavailable


class Logind:
    def all_locked(self, user: str) -> bool:
        """Count a locked interval only if no graphical session is unlocked."""
        dbus = importlib.import_module("dbus")
        bus = None
        deadline = time.monotonic() + 5
        try:
            bus = dbus.SystemBus(private=True)
            manager = dbus.Interface(
                bus.get_object(
                    "org.freedesktop.login1", "/org/freedesktop/login1", introspect=False
                ),
                "org.freedesktop.login1.Manager",
            )
            sessions = [row for row in manager.ListSessions(timeout=5) if row[2] == user]
            locked = []
            for row in sessions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Unavailable("Session observation deadline exceeded")
                properties = dbus.Interface(
                    bus.get_object("org.freedesktop.login1", row[4], introspect=False),
                    "org.freedesktop.DBus.Properties",
                ).GetAll("org.freedesktop.login1.Session", timeout=remaining)
                if properties.get("Type") in {"x11", "wayland", "mir"}:
                    locked.append(bool(properties.get("LockedHint")))
            return bool(locked) and all(locked)
        except Unavailable:
            raise
        except Exception as exc:
            raise Unavailable("Session state unavailable") from exc
        finally:
            if bus is not None:
                bus.close()
