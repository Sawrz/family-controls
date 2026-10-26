"""Durable command coordination and approved outbound device synchronization."""

import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..application import validate_command
from ..config import User
from ..domain import Conflict, InvalidCommand, midnight_after
from ..ports import Clock
from .accounts import Forbidden, Unauthorized, digest
from .models import BackendConfig, ChildPolicy, DeviceReport, Enrollment
from .ports import Store


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
        # Nix provides the initial desired policies. Later changes are centrally persisted.
        with store.transaction():
            for child, policy in config.children.items():
                validate_policy(policy.policy)
                existing = store.get("policies", child)
                if existing is None:
                    store.put("policies", child, {"revision": 1, **policy.model_dump()})
                elif (existing["device"], existing["user"]) != (policy.device, policy.user):
                    raise InvalidCommand(
                        "Device/user assignments cannot change under an existing child"
                    )

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
        return policy

    def set_policy(self, child: str, policy: ChildPolicy, revision: int, actor: str) -> dict:
        self.validate_policy(policy.policy)
        with self.store.transaction():
            old = self.policy(child)
            if old["revision"] != revision:
                raise Conflict("Policy changed; refresh before editing")
            if (old["device"], old["user"], old["timezone"]) != (
                policy.device,
                policy.user,
                policy.timezone,
            ):
                raise InvalidCommand("Device assignment and timezone are deployment settings")
            result = {"revision": revision + 1, **policy.model_dump()}
            self.store.put("policies", child, result)
            self.emit(
                "policy.changed",
                {"child": child, "actor": actor, "revision": revision + 1},
                old["device"],
            )
        return result

    def enrollment_token(self, device: str, actor: str) -> str:
        if device not in {c.device for c in self.config.children.values()}:
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
        record = self.store.get("devices", device)
        if not record or record["credential"] != digest(credential):
            raise Unauthorized("Invalid device credentials")
        if record["status"] != "approved":
            raise Forbidden("Device is not approved")
        return record

    def device_decision(self, device: str, approve: bool, actor: str) -> dict:
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

    def command(self, child: str, action: str, payload: dict, operation: str, actor: str) -> dict:
        validate_command(action, payload)
        if action in {"request-time", "cancel-request"}:
            raise Forbidden("Child requests originate on the authenticated device")
        if not operation or len(operation) > 128:
            raise InvalidCommand("Idempotency-Key of 1–128 characters required")
        fingerprint = digest(json.dumps([child, action, payload, actor], sort_keys=True))
        with self.store.transaction():
            policy = self.policy(child)
            previous = self.store.get("commands", operation)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Conflict("Operation identity belongs to another command")
                return {**self.status(child), "operation": previous, "duplicate": True}
            queued = self.store.pending_commands(policy["device"])
            if len(queued) >= 100:
                raise InvalidCommand("Device command queue is full; wait for delivery")
            request_id = payload.get("requestId") if action == "decide-request" else None
            if request_id:
                claim = self.store.get("decisions", child + ":" + request_id)
                if claim:
                    raise Conflict("This request already has a submitted decision")
                request = self.status(child).get("pendingRequest")
                if not request or request["id"] != request_id:
                    raise Conflict("Request is no longer pending")
                self.store.put("decisions", child + ":" + request_id, {"operationId": operation})
            now = self.clock.now()
            expires = min(
                now + timedelta(seconds=self.config.commandLifetimeSeconds),
                midnight_after(now.astimezone(ZoneInfo(policy["timezone"]))),
            )
            command = {
                "operationId": operation,
                "child": child,
                "device": policy["device"],
                "user": policy["user"],
                "action": action,
                "payload": payload,
                "actor": actor,
                "fingerprint": fingerprint,
                "createdAt": now.isoformat(),
                "expiresAt": expires.isoformat(),
                "status": "queued",
            }
            self.store.put("commands", operation, command)
            self.emit(
                "command.queued", {"child": child, "operationId": operation}, policy["device"]
            )
        return {**self.status(child), "operation": command, "duplicate": False}

    def status(self, child: str) -> dict:
        policy = self.policy(child)
        report = self.store.get("reports", child)
        status = (
            dict(report["status"])
            if report
            else {
                "user": policy["user"],
                "phase": "unknown",
                "application": {"status": "unknown"},
                "effectiveDailyLimitMinutes": policy["policy"]["playTime"]["dailyLimitMinutes"],
                "summary": {"playTimeLeft": "Unknown", "allowedLeft": "Unknown"},
            }
        )
        online = bool(
            report
            and (self.clock.now() - datetime.fromisoformat(report["at"])).total_seconds()
            <= self.config.staleSeconds
        )
        status["connectivity"] = "online" if online else "offline"
        status["lastSeen"] = report["at"] if report else None
        if not online:
            status.update(
                phase="unknown",
                remainingPlayTimeSeconds=None,
                remainingCountdownSeconds=None,
                remainingAllowedSeconds=None,
            )
            status["summary"] = {"playTimeLeft": "Unknown", "allowedLeft": "Unknown"}
        queued = [c for c in self.store.pending_commands(policy["device"]) if c["child"] == child]
        status["delivery"] = {"pending": len(queued), "status": "pending" if queued else "idle"}
        if queued:
            status["application"] = {**status["application"], "status": "pending"}
        device = self.store.get("devices", policy["device"]) or {}
        expected = sum(
            self.policy(name)["revision"]
            for name, item in self.config.children.items()
            if item.device == policy["device"]
        )
        status["policyDelivery"] = (
            "applied" if device.get("policyRevision") == expected else "pending"
        )
        if status["policyDelivery"] == "pending":
            status["application"] = {**status["application"], "status": "pending"}
        status["desiredPolicyRevision"] = policy["revision"]
        return status

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

    def sync(self, device: str, report: DeviceReport) -> dict:
        policies = {
            name: self.policy(name)
            for name, c in self.config.children.items()
            if c.device == device
        }
        users = {p["user"]: name for name, p in policies.items()}
        if set(report.statuses) - users.keys():
            raise Forbidden("Device cannot report another device's users")
        with self.store.transaction():
            now = self.clock.now()
            for user, status in report.statuses.items():
                child = users[user]
                previous = self.store.get("reports", child)
                self.store.put("reports", child, {"status": status, "at": now.isoformat()})
                # Publish meaningful request transitions, not a new event for every clock tick.
                if (previous or {}).get("status", {}).get("request") != status.get("request"):
                    self.emit("request.changed", {"child": child, "request": status.get("request")})
            for outcome in report.outcomes:
                command = self.store.get("commands", outcome.operationId)
                if not command or command["device"] != device:
                    raise Forbidden("Device cannot acknowledge another device's command")
                if command["status"] in {"applied", "superseded", "failed", "expired"}:
                    continue
                if command["status"] != outcome.status:
                    command.update(status=outcome.status, error=outcome.error)
                    self.store.put("commands", outcome.operationId, command)
                    if outcome.status in {"failed", "expired"}:
                        self.release_decision(command)
                    self.emit(
                        "command." + outcome.status,
                        {"child": command["child"], "operationId": outcome.operationId},
                    )
            commands = []
            for command in self.store.pending_commands(device):
                # Only the device can distinguish an expired undelivered command from
                # an already committed command whose acknowledgement was lost.
                account = self.store.get("accounts", command["actor"])
                commands.append(
                    {**command, "cancelled": not account or account["status"] != "approved"}
                )
            record = self.store.get("devices", device)
            if record is None:
                raise Unauthorized("Unknown device")
            record.update(lastSeen=now.isoformat(), policyRevision=report.policyRevision)
            self.store.put("devices", device, record)
        return {"policies": policies, "commands": commands[:100], "serverTime": now.isoformat()}

    def release_decision(self, command: dict) -> None:
        if command["action"] == "decide-request":
            key = command["child"] + ":" + command["payload"]["requestId"]
            self.store.delete("decisions", key)
