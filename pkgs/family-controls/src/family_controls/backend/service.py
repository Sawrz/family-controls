"""Durable command coordination and approved outbound device synchronization."""

import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..application import transition, validate_command
from ..config import User
from ..domain import (
    Conflict,
    InvalidCommand,
    Observation,
    Request,
    State,
    Unavailable,
    Window,
    duration,
    effective_policy,
    hhmm,
    midnight_after,
    state_from_dict,
)
from ..ports import Clock
from ..usage import Usage
from .accounts import Forbidden, Unauthorized, digest
from .models import BackendConfig, ChildPolicy, ChildSetup, DeviceReport, Enrollment
from .ports import Store
from .usage import UsageLedger


class Backend:
    def __init__(
        self,
        config: BackendConfig,
        store: Store,
        clock: Clock,
        validate_policy: Callable[[User], None],
    ) -> None:
        self.config, self.store, self.clock = config, store, clock
        self.validate_policy = validate_policy
        self.usage = UsageLedger(store, config.allocationSeconds)
        # Nix provides the initial desired policies. Later changes are centrally persisted.
        with store.transaction():
            for device, record in store.items("devices"):
                if record.get("managedBy") == "configuration":
                    trust = config.trustedDevices.get(device)
                    if trust is None or record["credential"] != trust.credentialHash:
                        record["status"] = "revoked"
                        store.put("devices", device, record)
            for child, policy in config.children.items():
                validate_policy(policy.policy)
                existing = store.get("policies", child)
                if existing is None:
                    store.put(
                        "policies",
                        child,
                        {
                            "revision": 1,
                            "setupComplete": "policy" in policy.model_fields_set,
                            **policy.model_dump(),
                        },
                    )
                elif "assignments" not in existing:
                    raise InvalidCommand(
                        "Legacy device-bound policies require an explicit shared-budget migration"
                    )
                elif existing["timezone"] != policy.timezone:
                    raise InvalidCommand(
                        "Changing a policy timezone requires explicit day reconciliation"
                    )
                elif existing["assignments"] != [a.model_dump() for a in policy.assignments]:
                    existing.update(
                        assignments=[a.model_dump() for a in policy.assignments],
                        revision=existing["revision"] + 1,
                    )
                    store.put("policies", child, existing)

    def emit(self, kind: str, data: dict, device: str | None = None) -> None:
        at = self.clock.now().isoformat()
        self.store.event("parents", kind, data, at)
        if device:
            self.store.event("device:" + device, kind, data, at)

    def policy(self, child: str) -> dict:
        if child not in self.config.children:
            raise KeyError(child)
        policy = self.store.get("policies", child)
        if policy is None:
            raise KeyError(child)
        return {"setupComplete": True, **policy}

    def setup_child(self, child: str, setup: ChildSetup, revision: int, actor: str) -> dict:
        """Confirm a child's first recurring policy once, with optimistic concurrency."""
        old = self.policy(child)
        if old["setupComplete"]:
            raise Conflict("Child setup is already complete; edit its recurring policy")
        candidate = ChildPolicy.model_validate(
            {
                key: old[key]
                for key in ("assignments", "timezone", "policy", "displayName")
                if key in old
            }
        )
        candidate.policy.playTime.dailyLimitMinutes = setup.dailyLimitMinutes
        candidate.policy.timeWindows = setup.timeWindows
        return self.set_policy(child, candidate, revision, actor, completing_setup=True)

    def set_policy(
        self,
        child: str,
        policy: ChildPolicy,
        revision: int,
        actor: str,
        *,
        completing_setup: bool = False,
    ) -> dict:
        self.validate_policy(policy.policy)
        with self.store.transaction():
            old = self.policy(child)
            if completing_setup and old["setupComplete"]:
                raise Conflict("Child setup is already complete")
            if not old["setupComplete"] and not completing_setup:
                raise Conflict("Complete child setup before editing its policy")
            if old["revision"] != revision:
                raise Conflict("Policy changed; refresh before editing")
            if (old["assignments"], old["timezone"]) != (
                [a.model_dump() for a in policy.assignments],
                policy.timezone,
            ):
                raise InvalidCommand("Assignments and timezone are deployment settings")
            result = {"revision": revision + 1, "setupComplete": True, **policy.model_dump()}
            self.store.put("policies", child, result)
            self.emit(
                "policy.changed",
                {"child": child, "actor": actor, "revision": revision + 1},
            )
        return result

    def enrollment_token(self, device: str, actor: str) -> str:
        if device in self.config.trustedDevices:
            raise Conflict("Device enrollment is managed by configuration")
        if device not in {a.device for c in self.config.children.values() for a in c.assignments}:
            raise InvalidCommand("Configure a child assigned to this device first")
        token = secrets.token_urlsafe(32)
        with self.store.transaction():
            self.store.put(
                "enrollment-tokens",
                digest(token),
                {
                    "device": device,
                    "expires": (self.clock.now() + timedelta(minutes=15)).isoformat(),
                },
            )
            self.store.event(
                "admin",
                "device.invited",
                {"device": device, "actor": actor},
                self.clock.now().isoformat(),
            )
        return token

    def enroll(self, request: Enrollment) -> dict:
        if request.device in self.config.trustedDevices:
            raise Conflict("Device enrollment is managed by configuration")
        with self.store.transaction():
            existing = self.store.get("devices", request.device)
            credential_hash = digest(request.credential.get_secret_value())
            if existing and existing["credential"] == credential_hash:
                return {"device": request.device, "status": existing["status"]}
            invitation = self.store.get(
                "enrollment-tokens", digest(request.token.get_secret_value())
            )
            if (
                not invitation
                or invitation["device"] != request.device
                or invitation["expires"] <= self.clock.now().isoformat()
            ):
                raise Unauthorized("Invalid or expired enrollment token")
            if existing and existing["status"] != "revoked":
                raise Conflict("Device is already enrolled")
            self.store.delete("enrollment-tokens", digest(request.token.get_secret_value()))
            self.store.put(
                "devices",
                request.device,
                {
                    "credential": credential_hash,
                    "status": "pending",
                    "policyRevision": 0,
                    "enrolledAt": self.clock.now().isoformat(),
                },
            )
            self.store.event(
                "admin", "device.pending", {"device": request.device}, self.clock.now().isoformat()
            )
        return {"device": request.device, "status": "pending"}

    def authenticate_device(self, device: str, credential: str) -> dict:
        """Authenticate and idempotently register a declaratively authorized device."""
        supplied = digest(credential)
        with self.store.transaction():
            record = self.store.get("devices", device)
            trust = self.config.trustedDevices.get(device)
            if trust:
                if not secrets.compare_digest(trust.credentialHash, supplied):
                    raise Unauthorized("Invalid device credentials")
                if (
                    not record
                    or record["credential"] != supplied
                    or record["status"] != "approved"
                    or record.get("managedBy") != "configuration"
                ):
                    record = {
                        **(record or {}),
                        "credential": supplied,
                        "status": "approved",
                        "managedBy": "configuration",
                        "policyRevision": (record or {}).get("policyRevision", 0),
                        "enrolledAt": (record or {}).get(
                            "enrolledAt", self.clock.now().isoformat()
                        ),
                    }
                    self.store.put("devices", device, record)
                    self.emit(
                        "device.approved", {"device": device, "actor": "configuration"}, device
                    )
                return record
            if not record or not secrets.compare_digest(record["credential"], supplied):
                raise Unauthorized("Invalid device credentials")
            if record.get("managedBy") == "configuration" or record["status"] != "approved":
                raise Forbidden("Device is not approved")
            return record

    def devices(self) -> list[dict]:
        """Expose enrollment state without credentials, including hosts not connected yet."""
        records = dict(self.store.items("devices"))
        for device in self.config.trustedDevices:
            records.setdefault(device, {"status": "configured", "enrolledAt": None})
        return [
            {
                **{k: v for k, v in record.items() if k != "credential"},
                "device": device,
                "managedBy": "configuration"
                if device in self.config.trustedDevices
                or record.get("managedBy") == "configuration"
                else "parent",
            }
            for device, record in records.items()
        ]

    def device_decision(self, device: str, approve: bool, actor: str) -> dict:
        if device in self.config.trustedDevices:
            raise Conflict("Device authorization is managed by configuration")
        with self.store.transaction():
            record = self.store.get("devices", device)
            if not record:
                raise KeyError(device)
            if approve and record["status"] != "pending":
                raise Conflict("Only pending devices can be approved")
            record["status"] = "approved" if approve else "revoked"
            self.store.put("devices", device, record)
            self.emit("device." + record["status"], {"device": device, "actor": actor}, device)
        return {"device": device, "status": record["status"]}

    def local_now(self, child: str) -> datetime:
        return self.clock.now().astimezone(ZoneInfo(self.policy(child)["timezone"]))

    def state(self, child: str) -> State:
        data = self.store.get("child-state", child)
        return (state_from_dict(data) if data else State()).today(self.local_now(child))

    def limit(self, child: str, state: State | None = None) -> int | None:
        state = state or self.state(child)
        return (
            None
            if state.unlimited
            else max(
                0,
                self.policy(child)["policy"]["playTime"]["dailyLimitMinutes"] * 60
                + state.delta_seconds,
            )
        )

    def command(self, child: str, action: str, payload: dict, operation: str, actor: str) -> dict:
        command = validate_command(action, payload)
        if action in {"request-time", "cancel-request"}:
            raise Forbidden("Child requests originate on the authenticated device")
        if not operation or len(operation) > 128:
            raise InvalidCommand("Idempotency-Key of 1–128 characters required")
        fingerprint = digest(json.dumps([child, action, payload, actor], sort_keys=True))
        with self.store.transaction():
            policy = self.policy(child)
            if not policy["setupComplete"]:
                raise Conflict("Complete child setup before granting access")
            previous = self.store.get("commands", operation)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Conflict("Operation identity belongs to another command")
                return {**self.status(child), "operation": previous, "duplicate": True}
            status = self.status(child)
            if action in {"set-remaining", "deduct-time"} and status["connectivity"] != "online":
                raise Unavailable("All assigned devices must report fresh usage")
            now = self.local_now(child)
            state = self.state(child)
            if (
                command.expectedRevision is not None
                and command.expectedRevision != policy["revision"]
            ):
                raise Conflict("Policy changed; refresh and try again")
            remaining = status["remainingPlayTimeSeconds"] or 0
            state = transition(
                User.model_validate(policy["policy"]),
                state,
                action,
                command,
                actor,
                now,
                Observation(remaining, 86400, now),
            )
            policy["revision"] += 1
            state.revision = policy["revision"]
            self.store.put("child-state", child, asdict(state))
            self.store.put("policies", child, policy)
            record = {
                "operationId": operation,
                "child": child,
                "action": action,
                "payload": payload,
                "actor": actor,
                "fingerprint": fingerprint,
                "createdAt": now.isoformat(),
                "expiresAt": midnight_after(now).isoformat(),
                "revision": policy["revision"],
                "status": "queued",
            }
            self.store.put("commands", operation, record)
            if state.request and action in {"decide-request", "block", "mute"}:
                self.emit(
                    "request.changed",
                    {"child": child, "request": asdict(state.request), "operationId": operation},
                )
            for assignment in policy["assignments"]:
                self.emit(
                    "command.queued",
                    {"child": child, "operationId": operation},
                    assignment["device"],
                )
        return {**self.status(child), "operation": record, "duplicate": False}

    def status(self, child: str) -> dict:
        policy, now, state = self.policy(child), self.local_now(child), self.state(child)
        devices = []
        for assignment in policy["assignments"]:
            report = self.store.get("reports", child + "/" + assignment["device"]) or {}
            fresh = (
                bool(report)
                and (self.clock.now() - datetime.fromisoformat(report["at"])).total_seconds()
                <= self.config.staleSeconds
            )
            device = self.store.get("devices", assignment["device"]) or {}
            fresh = fresh and device.get("status") == "approved"
            fresh = fresh and report.get("status", {}).get("connectivity") == "online"
            devices.append(
                {
                    **assignment,
                    "connectivity": "online" if fresh else "offline",
                    "lastSeen": report.get("at"),
                    "status": report.get("status", {}),
                    "policyRevision": report.get("revision", 0),
                }
            )
        online = bool(devices) and all(d["connectivity"] == "online" for d in devices)
        limit = self.limit(child, state)
        balance = self.usage.balance(child, now.date(), limit)
        pending = [
            d
            for d in devices
            if d["policyRevision"] != policy["revision"]
            or d["status"].get("application", {}).get("status") != "applied"
        ]
        remaining = (
            None if not online or limit is None else max(0, limit - balance["reportedUsedSeconds"])
        )
        request = state.request if state.request and state.request.active(now) else None
        last_seen = min((d["lastSeen"] for d in devices if d["lastSeen"]), default=None)
        user = User.model_validate(policy["policy"])
        allowed = user.timeWindows.for_day(now.isoweekday())
        homework = user.timeWindows.for_day(now.isoweekday(), homework=True)
        effective = effective_policy(
            allowed, homework, user.playTime.dailyLimitMinutes * 60, state, now
        )

        def windows(values: Sequence[Window]) -> list[str]:
            return [f"{hhmm(w.start)}–{hhmm(w.end)}" for w in values]

        return {
            "displayName": policy.get("displayName") or child[:1].upper() + child[1:],
            "timezone": policy["timezone"],
            "schedule": {
                "regular": windows(allowed),
                "today": windows(effective.windows),
                "unmetered": windows(effective.homework),
            },
            "user": child,
            "setupComplete": policy["setupComplete"],
            "revision": policy["revision"],
            "devices": devices,
            "phase": "blocked"
            if state.blocked_until
            else "unknown"
            if not online
            else "expired"
            if remaining == 0
            else "idle",
            "connectivity": "online" if online else "offline",
            "lastSeen": last_seen,
            "application": {
                "status": "pending" if pending or not devices else "applied",
                "error": None,
            },
            "delivery": {"pending": len(pending), "status": "pending" if pending else "idle"},
            "policyDelivery": "pending" if pending or not devices else "applied",
            "blocked": bool(state.blocked_until),
            "muted": state.muted,
            "unlimitedToday": state.unlimited,
            "effectiveDailyLimitMinutes": limit / 60 if limit is not None else None,
            "remainingPlayTimeSeconds": remaining,
            "remainingCountdownSeconds": remaining,
            "reportedUsedSeconds": balance["reportedUsedSeconds"],
            "countedLockedSeconds": sum(
                d["status"].get("countedLockedSeconds", 0) for d in devices
            ),
            "availableSeconds": balance["availableSeconds"],
            "reservedSeconds": balance["reservedSeconds"],
            "pendingRequest": {**asdict(request), "requestedAt": request.created_at}
            if request
            else None,
            "request": asdict(state.request) if state.request else None,
            "overrideExpiresAt": midnight_after(now).isoformat(),
            "summary": {
                "playTimeLeft": "Unlimited"
                if limit is None
                else duration(remaining)
                if remaining is not None
                else "Unknown",
                "allowedLeft": "See device status",
            },
        }

    def reconcile_notifications(self) -> None:
        """Persist time-driven outcomes so reconnecting interfaces can replace stale cards."""
        with self.store.transaction():
            for child in self.config.children:
                raw = self.store.get("child-state", child)
                state = self.state(child)
                if (
                    raw
                    and raw.get("request")
                    and raw["request"]["status"] == "pending"
                    and state.request
                    and state.request.status != "pending"
                ):
                    self.store.put("child-state", child, asdict(state))
                    self.emit("request.changed", {"child": child, "request": asdict(state.request)})
                for command in self.store.pending_commands(child):
                    if datetime.fromisoformat(command["expiresAt"]) <= self.clock.now():
                        command["status"] = "expired"
                        self.store.put("commands", command["operationId"], command)
                        self.emit(
                            "command.expired",
                            {"child": child, "operationId": command["operationId"]},
                        )

    def history(self, child: str) -> list[dict]:
        self.policy(child)
        return [
            {
                "at": c["createdAt"],
                "action": c["action"],
                "details": {"actor": c["actor"], "operationId": c["operationId"]},
                "outcome": c["status"],
                "error": c.get("error"),
            }
            for c in self.store.command_history(child)
        ]

    def week(self, child: str, start: date | None = None) -> dict:
        today = self.local_now(child).date()
        start = start or today - timedelta(days=today.weekday())
        return self.usage.week(child, start, today, self.policy(child)["assignments"])

    def sync(self, device: str, report: DeviceReport) -> dict:
        assignments = {
            a.user: name
            for name, c in self.config.children.items()
            for a in c.assignments
            if a.device == device
        }
        if set(report.statuses) - assignments.keys() or any(
            r.user not in assignments for r in report.usage
        ):
            raise Forbidden("Device cannot report another device's users")
        now = self.clock.now()
        with self.store.transaction():
            for usage in report.usage:
                child = assignments[usage.user]
                self.usage.accept(child, device, usage, self.local_now(child).date())
            policies = {}
            for user, child in assignments.items():
                policy, state = self.policy(child), self.state(child)
                status = report.statuses.get(user, {})
                incoming = status.get("request")
                if incoming and incoming.get("status") == "pending":
                    request = Request(**incoming)
                    seen = self.store.get("requests", child + "/" + request.id)
                    if not seen and request.active(self.local_now(child)):
                        self.store.put("requests", child + "/" + request.id, {"device": device})
                        if (
                            not state.muted
                            and not state.blocked_until
                            and not (state.request and state.request.active(self.local_now(child)))
                        ):
                            state.request = request
                            policy["revision"] += 1
                            self.store.put("child-state", child, asdict(state))
                            self.store.put("policies", child, policy)
                            self.emit(
                                "request.changed", {"child": child, "request": asdict(request)}
                            )
                elif (
                    incoming
                    and incoming.get("status") == "cancelled"
                    and state.request
                    and state.request.id == incoming.get("id")
                    and state.request.status == "pending"
                ):
                    state.request = Request(**incoming)
                    policy["revision"] += 1
                    self.store.put("child-state", child, asdict(state))
                    self.store.put("policies", child, policy)
                    self.emit("request.changed", {"child": child, "request": asdict(state.request)})
                today = self.local_now(child).date()
                key = self.usage.key(child, device, user, today)
                stored = self.store.get("usage", key)
                usage = (
                    Usage.model_validate(
                        {k: v for k, v in stored.items() if k not in {"child", "device"}}
                    )
                    if stored
                    else Usage(user=user, day=today)
                )
                allocation = self.usage.allocate(
                    child,
                    device,
                    usage,
                    today,
                    self.limit(child, state),
                    policy["revision"],
                    blocked=bool(state.blocked_until),
                )
                self.store.put(
                    "reports",
                    child + "/" + device,
                    {
                        "status": status,
                        "at": now.isoformat(),
                        "revision": usage.policyRevision,
                    },
                )
                policies[child] = {
                    **policy,
                    "user": user,
                    "device": device,
                    "state": asdict(state),
                    "allocation": allocation.model_dump(mode="json"),
                }
                for command in self.store.pending_commands(child):
                    deliveries = [
                        self.store.get("reports", child + "/" + a["device"]) or {}
                        for a in policy["assignments"]
                    ]
                    errors = [
                        d.get("status", {}).get("application", {}).get("error") for d in deliveries
                    ]
                    error = next((e for e in errors if e), None)
                    if error and command.get("error") != error:
                        command["error"] = error
                        self.store.put("commands", command["operationId"], command)
                        self.emit(
                            "command.failed",
                            {"child": child, "operationId": command["operationId"]},
                        )
                    if all(
                        d.get("revision", 0) >= command["revision"]
                        and d.get("status", {}).get("application", {}).get("status") == "applied"
                        for d in deliveries
                    ):
                        command["status"] = "applied"
                        command.pop("error", None)
                        self.store.put("commands", command["operationId"], command)
                        self.emit(
                            "command.applied",
                            {"child": child, "operationId": command["operationId"]},
                        )
            record = self.store.get("devices", device)
            if record is None:
                raise Unauthorized("Unknown device")
            record.update(lastSeen=now.isoformat(), policyRevision=report.policyRevision)
            self.store.put("devices", device, record)
        return {"policies": policies, "serverTime": now.isoformat()}
