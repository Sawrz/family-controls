"""Timekpr's structured D-Bus contract and shared boot configuration renderer."""

import argparse
import importlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import AgentConfig, User
from ..domain import InvalidCommand, Observation, Policy, Unavailable, Window
from ..ports import Clock


def hour_map(
    windows: tuple[Window, ...] | list[Window], homework: tuple[Window, ...] | list[Window]
) -> dict[str, dict[str, int]]:
    """Encode only representable policies; never open gaps or lose partial hours."""
    minutes = [0] * 1440
    for window in windows:
        for index in range(window.start // 60, window.end // 60):
            minutes[index] = 1
    for window in homework:
        for index in range(window.start // 60, window.end // 60):
            if minutes[index]:
                minutes[index] = 2
    result = {}
    for hour in range(24):
        values = minutes[hour * 60 : (hour + 1) * 60]
        used = [i for i, value in enumerate(values) if value]
        if not used:
            continue
        start, end = used[0], used[-1] + 1
        if len(set(values[start:end])) != 1:
            raise InvalidCommand(
                f"Timekpr cannot represent gaps or mixed homework within hour {hour:02d}; use hour-aligned transitions"
            )
        result[str(hour)] = {"STARTMIN": start, "ENDMIN": end, "UACC": int(values[start] == 2)}
    return result


def hours_string(hours: dict[str, dict[str, int]]) -> str:
    tokens = []
    for hour, value in hours.items():
        token = (
            hour
            if value["STARTMIN"] == 0 and value["ENDMIN"] == 60
            else f"{hour}[{value['STARTMIN']:02d}-{value['ENDMIN']:02d}]"
        )
        tokens.append(("!" if value["UACC"] else "") + token)
    return ";".join(tokens)


def validate_config(config: AgentConfig) -> None:
    for user in config.managedUsers.values():
        for day in range(1, 8):
            hour_map(user.timeWindows.for_day(day), user.timeWindows.for_day(day, homework=True))


class Timekpr:
    def __init__(self, config: AgentConfig, clock: Clock, bus_factory: Any = None) -> None:
        self.config, self.clock = config, clock
        self.dbus = importlib.import_module("dbus") if bus_factory is None else None
        self.bus_factory = bus_factory
        validate_config(config)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.bus_factory:
            yield self.bus_factory()
            return
        if self.dbus is None:
            raise Unavailable("D-Bus support is unavailable")
        bus = None
        try:
            bus = self.dbus.SystemBus(private=True)
            obj = bus.get_object("com.timekpr.server", "/com/timekpr/server", introspect=False)
            yield self.dbus.Interface(obj, "com.timekpr.server.user.admin")
        except Unavailable:
            raise
        except Exception as exc:
            raise Unavailable("Timekpr D-Bus connection unavailable") from exc
        finally:
            if bus is not None:
                bus.close()

    def call(self, interface: Any, method: str, *args: object) -> Any:
        try:
            result = getattr(interface, method)(*args, timeout=self.config.timekpr.timeoutSeconds)
        except Exception as exc:
            raise Unavailable(f"Timekpr {method} unavailable") from exc
        if not isinstance(result, (tuple, list)) or len(result) < 2 or result[0] != 0:
            raise Unavailable(f"Timekpr rejected {method}")
        return result[2] if len(result) > 2 else None

    def observe(self, user: str) -> Observation:
        with self.connection() as interface:
            raw = dict(self.call(interface, "getUserInformation", user, "F"))
        try:
            play = raw.get("ACTUAL_PLAYTIME_LEFT_DAY", raw.get("PLAYTIME_LEFT_DAY"))
            allowed = raw.get("ACTUAL_TIME_LEFT_CONTINUOUS", raw.get("TIME_LEFT_DAY"))
            if play is None or allowed is None:
                raise ValueError("Missing counters")
            return Observation(max(0, int(play)), max(0, int(allowed)), self.clock.now())
        except (ValueError, TypeError) as exc:
            raise Unavailable("Timekpr returned invalid usage counters") from exc

    def validate(self, policy: Policy) -> None:
        hour_map(policy.windows, policy.homework)

    def typed_hours(self, hours: dict) -> Any:
        return self.dbus.Dictionary(hours, signature="sa{si}") if self.dbus else hours

    def typed_limits(self, limits: list[int]) -> Any:
        return self.dbus.Array(limits, signature="i") if self.dbus else limits

    def apply(self, user: str, policy: Policy, now: datetime) -> None:
        """Set absolute policy, preserving measured usage; verify configuration readback."""
        cfg = self.config.managedUsers[user]
        limits = [cfg.playTime.dailyLimitMinutes * 60] * 7
        # Explicit domain unlimited maps to the maximum possible daily usage.
        limits[now.isoweekday() - 1] = (
            86400 if policy.limit_seconds is None else policy.limit_seconds
        )
        expected = {}
        with self.connection() as interface:
            current = self.call(interface, "getUserInformation", user, "F")
            for day in range(1, 8):
                hours = (
                    hour_map(policy.windows, policy.homework)
                    if day == now.isoweekday()
                    else hour_map(
                        cfg.timeWindows.for_day(day), cfg.timeWindows.for_day(day, homework=True)
                    )
                )
                expected[f"ALLOWED_HOURS_{day}"] = hours
                if current.get(f"ALLOWED_HOURS_{day}") != hours:
                    self.call(interface, "setAllowedHours", user, str(day), self.typed_hours(hours))
            if list(current.get("PLAYTIME_LIMITS_PER_WEEKDAYS", [])) != limits:
                self.call(interface, "setPlayTimeLimitsForDays", user, self.typed_limits(limits))
            actual = self.call(interface, "getUserInformation", user, "F")
            if list(actual.get("PLAYTIME_LIMITS_PER_WEEKDAYS", [])) != limits:
                raise Unavailable("Timekpr budget readback differs from desired policy")
            if any(actual.get(key) != hours for key, hours in expected.items()):
                raise Unavailable("Timekpr schedule readback differs from desired policy")

    def restore(self) -> None:
        now = self.clock.now()
        for user, cfg in self.config.managedUsers.items():
            self.apply(
                user,
                Policy(
                    tuple(cfg.timeWindows.for_day(now.isoweekday())),
                    tuple(cfg.timeWindows.for_day(now.isoweekday(), homework=True)),
                    cfg.playTime.dailyLimitMinutes * 60,
                    False,
                ),
                now,
            )


def render_user(name: str, cfg: User) -> str:
    """Render recurring policy only; existing Timekpr work/usage files are untouched."""
    lines = ["[DOCUMENTATION]", "#### generated by family controls", f"[{name}]"]
    for day in range(1, 8):
        hours = hour_map(cfg.timeWindows.for_day(day), cfg.timeWindows.for_day(day, homework=True))
        lines.append(f"ALLOWED_HOURS_{day} = {hours_string(hours)}")
    lines.extend(
        [
            "ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7",
            "LIMITS_PER_WEEKDAYS = " + ";".join(["86400"] * 7),
            f"TRACK_INACTIVE = {cfg.trackInactive}",
            "HIDE_TRAY_ICON = False",
            f"LOCKOUT_TYPE = {'kill' if cfg.lockoutAction == 'terminate' and cfg.killSessions else cfg.lockoutAction}",
            "WAKEUP_HOUR_INTERVAL = 0;23",
            f"[{name}.PLAYTIME]",
            "PLAYTIME_ENABLED = True",
            "PLAYTIME_LIMIT_OVERRIDE_ENABLED = False",
            "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED = False",
            "PLAYTIME_ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7",
            "PLAYTIME_LIMITS_PER_WEEKDAYS = "
            + ";".join([str(cfg.playTime.dailyLimitMinutes * 60)] * 7),
            "##PLAYTIME_ACTIVITIES## Do NOT remove or alter this line!",
            *[
                f"PLAYTIME_ACTIVITY_{index:03d} = {activity}"
                for index, activity in enumerate(cfg.playTime.activities, 1)
            ],
            "",
        ]
    )
    return "\n".join(lines)


def render_main() -> None:
    parser = argparse.ArgumentParser(
        description="Render recurring Timekpr configuration without changing usage"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--directory", default="/var/lib/timekpr/config")
    args = parser.parse_args()
    config = AgentConfig.model_validate_json(Path(args.config).read_text())
    validate_config(config)
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    for user, cfg in config.managedUsers.items():
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as temporary:
            temporary.write(render_user(user, cfg))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary.name, 0o644)
        os.replace(temporary.name, directory / f"timekpr.{user}.conf")
