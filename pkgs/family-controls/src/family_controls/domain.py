"""Pure policy calculations. All durations are seconds; windows are half-open."""

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta


class InvalidCommand(ValueError):
    """A command or policy cannot be interpreted safely."""


class Conflict(InvalidCommand):
    """The caller acted on a stale request or reused an operation identity."""


class Unavailable(RuntimeError):
    """An external service has not supplied trustworthy state."""


def minute(value: str) -> int:
    """Parse HH:MM, permitting 24:00 only as the end of a day."""
    if (
        len(value) != 5
        or value[2] != ":"
        or not (value[:2] + value[3:]).isascii()
        or not (value[:2] + value[3:]).isdecimal()
    ):
        raise InvalidCommand("Use HH:MM, from 00:00 to 24:00")
    try:
        hours, minutes = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise InvalidCommand("Use HH:MM") from exc
    if not (0 <= hours <= 24 and 0 <= minutes < 60) or (hours == 24 and minutes):
        raise InvalidCommand("Use HH:MM, from 00:00 to 24:00")
    return hours * 60 + minutes


def hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def duration(seconds: int | None) -> str:
    if seconds is None:
        return "Unlimited"
    minutes, seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True, order=True)
class Window:
    start: int
    end: int

    @classmethod
    def parse(cls, value: str) -> "Window":
        try:
            start, end = value.split("-")
            window = cls(minute(start) * 60, minute(end) * 60)
        except (ValueError, TypeError) as exc:
            raise InvalidCommand("Use a same-day window such as 09:00-18:00") from exc
        if not 0 <= window.start < window.end <= 86400:
            raise InvalidCommand("Window must end after it starts; split overnight windows by day")
        return window

    def contains(self, second: int) -> bool:
        return self.start <= second < self.end


def normalize(windows: list[Window]) -> list[Window]:
    result: list[Window] = []
    for window in sorted(windows):
        if result and window.start <= result[-1].end:
            result[-1] = Window(result[-1].start, max(result[-1].end, window.end))
        else:
            result.append(window)
    return result


def seconds_of_day(now: datetime) -> int:
    return now.hour * 3600 + now.minute * 60 + now.second


def midnight_after(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)


@dataclass
class Request:
    id: str
    created_at: str
    expires_at: str
    status: str = "pending"
    minutes: int | None = None
    reason: str = ""
    decided_by: str | None = None
    decision: str | None = None

    def active(self, now: datetime) -> bool:
        return self.status == "pending" and datetime.fromisoformat(self.expires_at) > now


@dataclass
class State:
    revision: int = 0
    day: str = ""
    delta_seconds: int = 0
    locked_seconds: int = 0
    unlimited: bool = False
    all_day: bool = False
    until: int | None = None
    extra_windows: list[Window] = field(default_factory=list)
    blocked_until: str | None = None
    muted: bool = False
    request: Request | None = None
    last_request_at: str | None = None

    def today(self, now: datetime) -> "State":
        current = self
        if self.day != now.date().isoformat():
            current = replace(
                self,
                day=now.date().isoformat(),
                delta_seconds=0,
                locked_seconds=0,
                unlimited=False,
                all_day=False,
                until=None,
                extra_windows=[],
            )
        if current.blocked_until and current.blocked_until < now.date().isoformat():
            current = replace(current, blocked_until=None)
        if (
            current.request
            and current.request.status == "pending"
            and not current.request.active(now)
        ):
            current = replace(current, request=replace(current.request, status="expired"))
        return current


def state_from_dict(data: dict) -> State:
    """Decode a persisted policy overlay without sharing mutable containers."""
    values = dict(data)
    values["extra_windows"] = [Window(**item) for item in data.get("extra_windows", [])]
    values["request"] = Request(**data["request"]) if data.get("request") else None
    return State(**values)


@dataclass(frozen=True)
class Policy:
    windows: tuple[Window, ...]
    homework: tuple[Window, ...]
    limit_seconds: int | None
    blocked: bool


def effective_policy(
    allowed: list[Window], homework: list[Window], base: int, state: State, now: datetime
) -> Policy:
    state = state.today(now)
    allowed = normalize([*allowed, *state.extra_windows])
    if state.until is not None:
        if not allowed:
            allowed = [Window(0, state.until)] if state.until else []
        elif state.until > allowed[-1].end:
            allowed = [*allowed[:-1], Window(allowed[-1].start, state.until)]
        else:
            allowed = [
                Window(w.start, min(w.end, state.until)) for w in allowed if w.start < state.until
            ]
    blocked = state.blocked_until is not None
    return Policy(
        tuple([] if blocked else allowed),
        tuple([] if state.all_day else homework),
        None if state.unlimited else max(0, base + state.delta_seconds - state.locked_seconds),
        blocked,
    )


@dataclass(frozen=True)
class Observation:
    play_left: int
    allowed_left: int
    observed_at: datetime
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    phase: str
    remaining: int
    inside: bool
    homework: bool
    cutoff: int | None


def decide(policy: Policy, observation: Observation, now: datetime, warning: int) -> Decision:
    second = seconds_of_day(now)
    window = next((w for w in policy.windows if w.contains(second)), None)
    homework = window is not None and any(w.contains(second) for w in policy.homework)
    if window is None:
        return Decision("blocked" if policy.blocked else "outside_schedule", 0, False, False, None)
    remaining = min(window.end - second, max(0, observation.allowed_left))
    if policy.limit_seconds is not None and not homework:
        remaining = min(remaining, max(0, observation.play_left))
    phase = "expired" if remaining == 0 else "countdown_active" if remaining <= warning else "idle"
    return Decision(phase, remaining, True, homework, window.end)


def block_date(now: datetime, days: int) -> str:
    return (now.date() + timedelta(days=days - 1)).isoformat()


def valid_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def parse_duration(value: str, *, allow_zero: bool = False) -> int:
    """Parse integer minutes, 25m, 1h, or 1h 10m without accepting negative input."""
    value = value.strip().lower()
    if value.isascii() and value.isdecimal():
        minutes = int(value)
    else:
        match = re.fullmatch(r"(?:(\d{1,3})h)?\s*(?:(\d{1,4})m)?", value)
        if not match or not any(match.groups()):
            raise InvalidCommand("Enter minutes, 25m, or 1h 10m")
        minutes = int(match[1] or 0) * 60 + int(match[2] or 0)
    if not (0 if allow_zero else 1) <= minutes <= 1440:
        raise InvalidCommand("Choose a duration up to 24 hours")
    return minutes
