#!/usr/bin/env python3
import argparse
import contextlib
import datetime as dt
import grp
import http.server
import json
import os
import socketserver
import sqlite3
import threading
import time
from typing import Any, cast

from common import (
    classify_time,
    ensure_dir,
    format_duration,
    json_response,
    load_json,
    local_now,
    parse_timekpr_info,
    read_json_body,
    run_checked,
    safe_int,
    timekpr_hours_string,
    today_key,
    weekday_key,
    windows_for_day,
    with_overlay,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
  user TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user, key)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  user TEXT NOT NULL,
  action TEXT NOT NULL,
  details TEXT NOT NULL
);
"""


class Agent:
    def __init__(self, config: dict[str, Any], token: str) -> None:
        self.config = config
        self.token = token.strip()
        self.state_dir = config["agent"]["stateDir"]
        self.socket_path = config["agent"]["socketPath"]
        self.sqlite_path = os.path.join(self.state_dir, "state.sqlite")
        self.timezone = config["timezone"]
        self.poll_interval = int(config["pollIntervalSeconds"])
        self.warning_lead = int(config["warningLeadSeconds"])
        self.users = config["managedUsers"]
        self.primary_user = next(iter(self.users))
        ensure_dir(self.state_dir)
        ensure_dir(os.path.dirname(self.socket_path))
        self.db = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.lock = threading.Lock()

    def get_state(self, user: str, key: str, default: str | None = None) -> str | None:
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM state WHERE user = ? AND key = ?",
                (user, key),
            ).fetchone()
        return default if row is None else row[0]

    def set_state(self, user: str, key: str, value: Any) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO state(user, key, value) VALUES(?, ?, ?) "
                "ON CONFLICT(user, key) DO UPDATE SET value = excluded.value",
                (user, key, json.dumps(value) if not isinstance(value, str) else value),
            )
            self.db.commit()

    def delete_state(self, user: str, key: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM state WHERE user = ? AND key = ?", (user, key))
            self.db.commit()

    def append_audit(self, user: str, action: str, details: dict[str, Any]) -> None:
        now = local_now(self.timezone).isoformat()
        with self.lock:
            self.db.execute(
                "INSERT INTO audit_log(created_at, user, action, details) VALUES(?, ?, ?, ?)",
                (now, user, action, json.dumps(details, sort_keys=True)),
            )
            self.db.commit()

    def _timekpr(self, *args: str) -> dict[str, str]:
        command = [self.config["timekpr"]["adminBinary"], *args]
        completed = run_checked(command)
        return parse_timekpr_info(completed.stdout)

    def _timekpr_cmd(self, *args: str) -> None:
        command = [self.config["timekpr"]["adminBinary"], *args]
        run_checked(command)

    def _overlay(self, user: str, now: dt.datetime) -> dict[str, Any]:
        raw = self.get_state(user, "effective_overlay")
        if raw is None:
            return {}
        value = json.loads(raw)
        if value.get("date") != today_key(now):
            self.delete_state(user, "effective_overlay")
            return {}
        return value

    def _effective_allowed_windows(self, user: str, now: dt.datetime) -> list[Any]:
        user_cfg = self.users[user]
        overlay = self._overlay(user, now)
        allowed = windows_for_day(user_cfg["timeWindows"]["allowed"], now)
        return with_overlay(allowed, overlay.get("until"))

    def _effective_unmetered_windows(self, user: str, now: dt.datetime) -> list[Any]:
        return windows_for_day(self.users[user]["timeWindows"]["unmetered"], now, unmetered=True)

    def _effective_daily_limit_minutes(self, user: str, now: dt.datetime) -> int:
        base = int(self.users[user]["playTime"]["dailyLimitMinutes"])
        overlay = self._overlay(user, now)
        return max(0, base + int(overlay.get("capMinutes", 0)))

    def _set_overlay(
        self,
        user: str,
        now: dt.datetime,
        *,
        until: str | None = None,
        cap_minutes: int | None = None,
    ) -> dict[str, Any]:
        existing = self._overlay(user, now)
        overlay = {
            "date": today_key(now),
            "until": existing.get("until"),
            "capMinutes": int(existing.get("capMinutes", 0)),
        }
        if until is not None:
            overlay["until"] = until
        if cap_minutes is not None:
            overlay["capMinutes"] = cap_minutes
        self.set_state(user, "effective_overlay", overlay)
        return overlay

    def _shift_allowed_until(self, user: str, now: dt.datetime, minutes: int) -> str:
        existing = self._overlay(user, now)
        if existing.get("until"):
            base_time = dt.datetime.combine(now.date(), dt.time.fromisoformat(existing["until"]))
        else:
            allowed = windows_for_day(self.users[user]["timeWindows"]["allowed"], now)
            if allowed:
                base_time = dt.datetime.combine(
                    now.date(),
                    dt.time(
                        allowed[-1].end_seconds // 3600, (allowed[-1].end_seconds % 3600) // 60
                    ),
                )
            else:
                base_time = now
        shifted = max(
            now.replace(hour=0, minute=0, second=0, microsecond=0),
            min(
                base_time + dt.timedelta(minutes=minutes),
                now.replace(hour=23, minute=59, second=0, microsecond=0),
            ),
        )
        return shifted.strftime("%H:%M")

    def _extend_allowed_until(self, user: str, now: dt.datetime, minutes: int) -> str:
        return self._shift_allowed_until(user, now, minutes)

    def _reduce_allowed_until(self, user: str, now: dt.datetime, minutes: int) -> str:
        return self._shift_allowed_until(user, now, -minutes)

    @staticmethod
    def _format_day_time(seconds: int) -> str:
        seconds = max(0, min(seconds, 24 * 3600))
        if seconds == 24 * 3600:
            return "24:00"
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"

    def _blocked_until_date(self, user: str, now: dt.datetime) -> str | None:
        blocked_until = self.get_state(user, "blocked_until_date")
        if blocked_until is None:
            return None
        if blocked_until < today_key(now):
            self.delete_state(user, "blocked_until_date")
            return None
        return blocked_until

    def _validate_block_days(self, payload: dict[str, Any]) -> int:
        if payload.get("today", False):
            return 1
        if "days" not in payload:
            raise ValueError("days is required")
        days = payload["days"]
        if isinstance(days, bool) or not isinstance(days, int):
            raise ValueError("days must be an integer from 1 to 31")
        if days < 1 or days > 31:
            raise ValueError("days must be an integer from 1 to 31")
        return days

    def _locked(self, user: str) -> bool:
        try:
            command = [
                self.config["system"]["loginctlBinary"],
                "show-user",
                user,
                "--property=Linger",
                "--property=Display",
                "--property=State",
                "--property=Sessions",
                "--no-page",
            ]
            completed = run_checked(command)
            session_ids = []
            for line in completed.stdout.splitlines():
                if line.startswith("Sessions="):
                    session_ids = [token for token in line.split("=", 1)[1].split(" ") if token]
            for session_id in session_ids:
                completed = run_checked(
                    [
                        self.config["system"]["loginctlBinary"],
                        "show-session",
                        session_id,
                        "--property=LockedHint",
                        "--property=Active",
                        "--no-page",
                    ]
                )
                values = {}
                for line in completed.stdout.splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        values[key] = value
                if values.get("Active") == "yes" and values.get("LockedHint") == "yes":
                    return True
        except Exception:
            return False
        return False

    def _apply_runtime_policy(self, user: str, now: dt.datetime) -> None:
        day = weekday_key(now)
        if self._blocked_until_date(user, now) is not None:
            self._timekpr_cmd("--setallowedhours", user, day, "")
            return

        allowed_strings = timekpr_hours_string(
            self._effective_allowed_windows(user, now),
            self._effective_unmetered_windows(user, now),
        )
        playtime_limit_seconds = str(self._effective_daily_limit_minutes(user, now) * 60)
        self._timekpr_cmd("--setallowedhours", user, day, allowed_strings)
        self._timekpr_cmd("--setplaytimelimits", user, ";".join([playtime_limit_seconds] * 7))

    def _count_locked_playtime(self, user: str) -> None:
        user_cfg = self.users[user]
        if not user_cfg.get("countLockedTime", False):
            return
        if not self._locked(user):
            return
        self._timekpr_cmd("--setplaytimeleft", user, "-", str(self.poll_interval))

    def _terminate_sessions(self, user: str) -> None:
        if not self.users[user].get("killSessions", False):
            return
        marker = self.get_state(user, "expired_enforced")
        today_marker = local_now(self.timezone).isoformat()[:16]
        if marker == today_marker:
            return
        run_checked([self.config["system"]["loginctlBinary"], "terminate-user", user])
        self.set_state(user, "expired_enforced", today_marker)

    def _status(self, user: str, now: dt.datetime | None = None) -> dict[str, Any]:
        now = now or local_now(self.timezone)
        timekpr = self._timekpr("--userinfo", user)
        user_cfg = self.users[user]
        blocked_until_date = self._blocked_until_date(user, now)
        blocked = blocked_until_date is not None
        allowed = [] if blocked else self._effective_allowed_windows(user, now)
        allowed_until = None if not allowed else self._format_day_time(allowed[-1].end_seconds)
        unmetered = self._effective_unmetered_windows(user, now)
        time_classification = classify_time(now=now, allowed=allowed, unmetered=unmetered)
        playtime_left = safe_int(
            timekpr.get("PLAYTIME_LEFT_DAY", timekpr.get("ACTUAL_PLAYTIME_LEFT_DAY"))
        )
        allowed_left = safe_int(
            timekpr.get("TIME_LEFT_DAY", timekpr.get("ACTUAL_TIME_LEFT_CONTINUOUS"))
        )
        pending_request = self.get_state(user, "pending_request")
        muted = self.get_state(user, "requests_muted") is not None

        expired = (
            not time_classification["inside_allowed"]
            or allowed_left <= 0
            or (not time_classification["inside_unmetered"] and playtime_left <= 0)
        )

        if blocked:
            desired_dns_profile = user_cfg["dnsProfiles"]["restrictedProfile"]
        elif expired:
            desired_dns_profile = user_cfg["dnsProfiles"]["restrictedProfile"]
        elif time_classification["inside_unmetered"]:
            desired_dns_profile = user_cfg["dnsProfiles"]["homeworkProfile"]
        else:
            desired_dns_profile = user_cfg["dnsProfiles"]["leisureProfile"]

        remaining_for_countdown = allowed_left
        if not time_classification["inside_unmetered"]:
            positive_limits = [value for value in [allowed_left, playtime_left] if value > 0]
            remaining_for_countdown = min(positive_limits) if positive_limits else 0

        if blocked:
            phase = "blocked"
            remaining_for_countdown = 0
        elif expired:
            phase = "expired"
        elif muted:
            phase = "muted"
        elif pending_request is not None:
            phase = "pending_request"
        elif remaining_for_countdown <= self.warning_lead:
            phase = "countdown_active"
        else:
            phase = "idle"

        if phase == "countdown_active":
            countdown_started_at = self.get_state(user, "countdown_started_at")
            if countdown_started_at is None:
                self.set_state(user, "countdown_started_at", now.isoformat())
        else:
            self.delete_state(user, "countdown_started_at")

        self.set_state(user, "desired_dns_profile", desired_dns_profile)

        return {
            "user": user,
            "phase": phase,
            "blocked": blocked,
            "blockedUntilDate": blocked_until_date,
            "muted": muted,
            "pendingRequest": None if pending_request is None else json.loads(pending_request),
            "desiredDnsProfile": desired_dns_profile,
            "remainingPlayTimeSeconds": playtime_left,
            "remainingAllowedSeconds": allowed_left,
            "allowedUntil": allowed_until,
            "remainingCountdownSeconds": max(0, remaining_for_countdown),
            "insideAllowedWindow": time_classification["inside_allowed"],
            "insideUnmeteredWindow": time_classification["inside_unmetered"],
            "effectiveDailyLimitMinutes": self._effective_daily_limit_minutes(user, now),
            "timekpr": timekpr,
            "summary": {
                "playTimeLeft": format_duration(playtime_left),
                "allowedLeft": format_duration(allowed_left),
                "allowedUntil": allowed_until,
            },
        }

    @staticmethod
    def _validate_positive_minutes(minutes: int) -> None:
        if minutes <= 0:
            raise ValueError("minutes must be positive")

    def _payload_minutes(self, payload: dict[str, Any]) -> int:
        minutes = payload.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ValueError("minutes must be a positive integer")
        self._validate_positive_minutes(minutes)
        return minutes

    def request_time(self, user: str) -> dict[str, Any]:
        now = local_now(self.timezone)
        status = self._status(user, now)
        if status["phase"] in {"blocked", "expired", "muted", "pending_request"}:
            return status
        payload = {"requestedAt": now.isoformat()}
        self.set_state(user, "pending_request", payload)
        self.append_audit(user, "request-time", payload)
        return self._status(user, now)

    def add_time(self, user: str, minutes: int) -> dict[str, Any]:
        self._validate_positive_minutes(minutes)
        now = local_now(self.timezone)
        existing = self._overlay(user, now)
        overlay = self._set_overlay(
            user,
            now,
            cap_minutes=int(existing.get("capMinutes", 0)) + minutes,
        )
        self._apply_runtime_policy(user, now)
        self.delete_state(user, "pending_request")
        self.append_audit(
            user, "add-time", {"minutes": minutes, "capMinutes": overlay["capMinutes"]}
        )
        return self._status(user, now)

    def deduct_time(self, user: str, minutes: int) -> dict[str, Any]:
        self._validate_positive_minutes(minutes)
        now = local_now(self.timezone)
        status = self._status(user, now)
        remaining_seconds = max(0, int(status["remainingPlayTimeSeconds"]))
        applied_minutes = min(minutes, remaining_seconds // 60)
        existing = self._overlay(user, now)
        overlay = self._set_overlay(
            user,
            now,
            cap_minutes=int(existing.get("capMinutes", 0)) - applied_minutes,
        )
        self._apply_runtime_policy(user, now)
        self.delete_state(user, "pending_request")
        self.append_audit(
            user,
            "deduct-time",
            {
                "requestedMinutes": minutes,
                "appliedMinutes": applied_minutes,
                "capMinutes": overlay["capMinutes"],
            },
        )
        return self._status(user, now)

    def extend_access(
        self,
        user: str,
        minutes: int,
        *,
        until: str | None = None,
    ) -> dict[str, Any]:
        self._validate_positive_minutes(minutes)
        now = local_now(self.timezone)
        if self._blocked_until_date(user, now) is not None:
            raise ValueError("screen time is blocked; unblock first")
        overlay = self._set_overlay(
            user,
            now,
            until=until or self._extend_allowed_until(user, now, minutes),
        )
        self._apply_runtime_policy(user, now)
        self.delete_state(user, "pending_request")
        self.append_audit(
            user,
            "extend-access",
            {"minutes": minutes, "until": overlay["until"]},
        )
        return self._status(user, now)

    def reduce_access(self, user: str, minutes: int) -> dict[str, Any]:
        self._validate_positive_minutes(minutes)
        now = local_now(self.timezone)
        if self._blocked_until_date(user, now) is not None:
            raise ValueError("screen time is blocked; unblock first")
        overlay = self._set_overlay(
            user,
            now,
            until=self._reduce_allowed_until(user, now, minutes),
        )
        self._apply_runtime_policy(user, now)
        self.delete_state(user, "pending_request")
        self.append_audit(
            user,
            "reduce-access",
            {"minutes": minutes, "until": overlay["until"]},
        )
        return self._status(user, now)

    def mute(self, user: str) -> dict[str, Any]:
        now = local_now(self.timezone)
        self.set_state(user, "requests_muted", "1")
        self.delete_state(user, "pending_request")
        self.append_audit(user, "mute", {})
        return self._status(user, now)

    def unmute(self, user: str) -> dict[str, Any]:
        now = local_now(self.timezone)
        self.delete_state(user, "requests_muted")
        self.append_audit(user, "unmute", {})
        return self._status(user, now)

    def block(self, user: str, days: int) -> dict[str, Any]:
        now = local_now(self.timezone)
        until_date = (now.date() + dt.timedelta(days=days - 1)).isoformat()
        self.set_state(user, "blocked_until_date", until_date)
        for key in ["pending_request", "effective_overlay"]:
            self.delete_state(user, key)
        self._apply_runtime_policy(user, now)
        self.append_audit(user, "block", {"days": days, "untilDate": until_date})
        return self._status(user, now)

    def unblock(self, user: str) -> dict[str, Any]:
        now = local_now(self.timezone)
        for key in ["pending_request", "blocked_until_date"]:
            self.delete_state(user, key)
        self._apply_runtime_policy(user, now)
        self.append_audit(user, "unblock", {})
        return self._status(user, now)

    def housekeeping(self) -> None:
        while True:
            now = local_now(self.timezone)
            for user in self.users:
                with contextlib.suppress(Exception):
                    self._apply_runtime_policy(user, now)
                    self._count_locked_playtime(user)
                    status = self._status(user, now)
                    if status["phase"] in {"expired", "blocked"}:
                        self._terminate_sessions(user)
            time.sleep(self.poll_interval)


class BaseHandler(http.server.BaseHTTPRequestHandler):
    server_version = "family-controlsd/0.1"

    def do_GET(self) -> None:
        cast("UnixHTTPServer | TCPHTTPServer", self.server).agent_handle(self, "GET")

    def do_POST(self) -> None:
        cast("UnixHTTPServer | TCPHTTPServer", self.server).agent_handle(self, "POST")

    def log_message(self, format: str, *args: Any) -> None:
        return


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, handler, agent: Agent):
        if os.path.exists(path):
            os.unlink(path)
        self.agent = agent
        super().__init__(path, handler)
        group_name = self.agent.config["agent"].get("socketGroup")
        if group_name:
            gid = grp.getgrnam(group_name).gr_gid
            os.chown(path, 0, gid)
        os.chmod(path, 0o660)

    def agent_handle(self, handler: BaseHandler, method: str) -> None:
        if handler.path == "/v1/status" and method == "GET":
            json_response(handler, self.agent._status(self.agent.primary_user))
            return
        if handler.path == "/v1/request-time" and method == "POST":
            json_response(handler, self.agent.request_time(self.agent.primary_user))
            return
        json_response(handler, {"error": "not-found"}, status=404)


class TCPHTTPServer(http.server.ThreadingHTTPServer):
    def __init__(self, address, handler, agent: Agent):
        self.agent = agent
        super().__init__(address, handler)

    def agent_handle(self, handler: BaseHandler, method: str) -> None:
        auth = handler.headers.get("Authorization", "")
        if auth != f"Bearer {self.agent.token}":
            json_response(handler, {"error": "unauthorized"}, status=401)
            return

        path = handler.path.rstrip("/")
        if path.startswith("/v1/users/") and path.endswith("/status") and method == "GET":
            user = path.split("/")[3]
            json_response(handler, self.agent._status(user))
            return
        if path.startswith("/v1/users/") and method == "POST":
            user = path.split("/")[3]
            action = path.split("/")[4]
            payload = read_json_body(handler)
            try:
                if action == "add-time":
                    json_response(
                        handler, self.agent.add_time(user, self.agent._payload_minutes(payload))
                    )
                    return
                if action == "deduct-time":
                    json_response(
                        handler, self.agent.deduct_time(user, self.agent._payload_minutes(payload))
                    )
                    return
                if action == "extend-access":
                    json_response(
                        handler,
                        self.agent.extend_access(
                            user,
                            self.agent._payload_minutes(payload),
                            until=payload.get("until"),
                        ),
                    )
                    return
                if action == "reduce-access":
                    json_response(
                        handler,
                        self.agent.reduce_access(user, self.agent._payload_minutes(payload)),
                    )
                    return
                if action == "mute":
                    json_response(handler, self.agent.mute(user))
                    return
                if action == "unmute":
                    json_response(handler, self.agent.unmute(user))
                    return
                if action == "block":
                    json_response(
                        handler, self.agent.block(user, self.agent._validate_block_days(payload))
                    )
                    return
                if action == "unblock":
                    json_response(handler, self.agent.unblock(user))
                    return
            except Exception as exc:
                json_response(handler, {"error": str(exc)}, status=400)
                return
        json_response(handler, {"error": "not-found"}, status=404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()

    config = load_json(args.config)
    with open(args.token_file, "r", encoding="utf-8") as fh:
        token = fh.read().strip()

    agent = Agent(config, token)
    threading.Thread(target=agent.housekeeping, daemon=True).start()

    unix_server = UnixHTTPServer(agent.socket_path, BaseHandler, agent)
    threading.Thread(target=unix_server.serve_forever, daemon=True).start()

    tcp_server = TCPHTTPServer(("0.0.0.0", int(config["agent"]["port"])), BaseHandler, agent)
    cert_path = config["agent"]["tlsCertPath"]
    key_path = config["agent"]["tlsKeyPath"]
    context = __import__("ssl").SSLContext(__import__("ssl").PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    tcp_server.socket = context.wrap_socket(tcp_server.socket, server_side=True)
    tcp_server.serve_forever()


if __name__ == "__main__":
    main()
