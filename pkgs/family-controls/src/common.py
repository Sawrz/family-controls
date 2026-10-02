import dataclasses
import datetime as dt
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import urllib.parse
import urllib.request
from typing import Any


TIME_RE = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2})$")


def json_response(handler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_hhmm(value: str) -> dt.time:
    match = TIME_RE.match(value)
    if match is None:
        raise ValueError(f"invalid HH:MM value: {value}")
    return dt.time(int(match.group("hour")), int(match.group("minute")))


def seconds_since_midnight(ts: dt.datetime) -> int:
    return ts.hour * 3600 + ts.minute * 60 + ts.second


def today_key(now: dt.datetime) -> str:
    return now.date().isoformat()


def weekday_key(now: dt.datetime) -> str:
    return str(now.isoweekday())


def local_now(timezone: str) -> dt.datetime:
    if hasattr(dt, "zoneinfo"):
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo(timezone))
    return dt.datetime.now()


@dataclasses.dataclass(frozen=True)
class Window:
    start_seconds: int
    end_seconds: int
    unmetered: bool = False

    def contains(self, second_of_day: int) -> bool:
        return self.start_seconds <= second_of_day < self.end_seconds

    def remaining(self, second_of_day: int) -> int:
        if not self.contains(second_of_day):
            return 0
        return self.end_seconds - second_of_day


def parse_window_range(value: str, *, unmetered: bool = False) -> Window:
    start_raw, end_raw = value.split("-", 1)
    start = parse_hhmm(start_raw)
    end = parse_hhmm(end_raw)
    start_seconds = start.hour * 3600 + start.minute * 60
    end_seconds = end.hour * 3600 + end.minute * 60
    if end_seconds <= start_seconds:
        raise ValueError(f"window must end after start: {value}")
    return Window(start_seconds=start_seconds, end_seconds=end_seconds, unmetered=unmetered)


def windows_for_day(
    window_map: dict[str, list[str]], now: dt.datetime, *, unmetered: bool = False
) -> list[Window]:
    values = window_map.get(weekday_key(now), window_map.get("ALL", []))
    return [parse_window_range(value, unmetered=unmetered) for value in values]


def with_overlay(
    allowed: list[Window],
    overlay_until: str | None,
) -> list[Window]:
    if overlay_until is None:
        return allowed
    until = parse_hhmm(overlay_until)
    until_seconds = until.hour * 3600 + until.minute * 60
    if not allowed:
        if until_seconds <= 0:
            return []
        return [Window(start_seconds=0, end_seconds=until_seconds, unmetered=False)]

    if until_seconds > allowed[-1].end_seconds:
        merged = list(allowed)
        last = merged[-1]
        merged[-1] = Window(
            start_seconds=last.start_seconds,
            end_seconds=until_seconds,
            unmetered=last.unmetered,
        )
        return merged

    merged = []
    for window in allowed:
        if window.start_seconds >= until_seconds:
            break
        end_seconds = min(window.end_seconds, until_seconds)
        if end_seconds > window.start_seconds:
            merged.append(
                Window(
                    start_seconds=window.start_seconds,
                    end_seconds=end_seconds,
                    unmetered=window.unmetered,
                )
            )
    return merged


def classify_time(
    *,
    now: dt.datetime,
    allowed: list[Window],
    unmetered: list[Window],
) -> dict[str, Any]:
    second_of_day = seconds_since_midnight(now)
    allowed_window = next((window for window in allowed if window.contains(second_of_day)), None)
    unmetered_window = next(
        (window for window in unmetered if window.contains(second_of_day)), None
    )

    if allowed_window is None:
        return {
            "inside_allowed": False,
            "inside_unmetered": False,
            "remaining_allowed_seconds": 0,
        }

    return {
        "inside_allowed": True,
        "inside_unmetered": unmetered_window is not None,
        "remaining_allowed_seconds": allowed_window.remaining(second_of_day),
    }


def parse_timekpr_info(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def run_checked(
    command: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True, env=env)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def http_request(
    url: str,
    *,
    method: str = "GET",
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    verify: bool = False,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    if json_payload is not None:
        data = json.dumps(json_payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    context = None
    if url.startswith("https://") and not verify:
        context = ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = response.read()
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def unix_request(
    socket_path: str, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection = UnixHTTPConnection(socket_path)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, remaining = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def safe_int(value: str | None, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def minutes_state(
    allowed_windows: list[Window],
    unmetered_windows: list[Window],
) -> list[int]:
    minutes = [0] * 1440
    for window in allowed_windows:
        start = window.start_seconds // 60
        end = window.end_seconds // 60
        for minute in range(start, end):
            minutes[minute] = 1
    for window in unmetered_windows:
        start = window.start_seconds // 60
        end = window.end_seconds // 60
        for minute in range(start, end):
            minutes[minute] = 2
    return minutes


def _minute_token(hour: int, start_minute: int, end_minute: int, *, unmetered: bool) -> str:
    if start_minute == 0 and end_minute == 60:
        token = f"{hour}"
    else:
        token = f"{hour}[{start_minute:02d}-{end_minute:02d}]"
    return f"!{token}" if unmetered else token


def timekpr_hours_string(allowed_windows: list[Window], unmetered_windows: list[Window]) -> str:
    minutes = minutes_state(allowed_windows, unmetered_windows)
    tokens: list[str] = []
    minute = 0
    while minute < 1440:
        state = minutes[minute]
        if state == 0:
            minute += 1
            continue
        end = minute
        while end < 1440 and minutes[end] == state:
            end += 1
        cursor = minute
        while cursor < end:
            hour = cursor // 60
            hour_end = min(end, (hour + 1) * 60)
            tokens.append(
                _minute_token(
                    hour,
                    cursor % 60,
                    hour_end % 60 if hour_end % 60 != 0 else 60,
                    unmetered=state == 2,
                )
            )
            cursor = hour_end
        minute = end
    return ";".join(tokens)
