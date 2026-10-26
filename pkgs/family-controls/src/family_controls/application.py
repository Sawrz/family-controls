"""Serialized user workflows, durable command receipts, and read-only status."""

import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from pydantic import Field, ValidationError

from .config import AgentConfig, Model, User
from .domain import (
    Conflict,
    InvalidCommand,
    Observation,
    Policy,
    Request,
    State,
    Unavailable,
    Window,
    block_date,
    decide,
    duration,
    effective_policy,
    hhmm,
    midnight_after,
    minute,
    normalize,
    seconds_of_day,
)
from .ports import Clock, Enforcement, Sessions, StateRepository

LOG = logging.getLogger(__name__)


class Command(Model):
    minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    until: str | None = None
    days: Annotated[int, Field(strict=True, ge=1, le=31)] | None = None
    today: Annotated[bool, Field(strict=True)] = False
    requestId: Annotated[str, Field(max_length=64)] | None = None
    reason: Annotated[str, Field(max_length=300)] = ""
    decision: str | None = None
    expectedRevision: Annotated[int, Field(strict=True, ge=0)] | None = None


ADJUSTMENTS = {
    "add-time",
    "deduct-time",
    "extend-access",
    "reduce-access",
    "set-limit",
    "set-remaining",
    "grant-session",
}
ACTIONS = ADJUSTMENTS | {
    "allow-until",
    "unlimited-today",
    "allow-today",
    "restore-today",
    "block",
    "unblock",
    "mute",
    "unmute",
    "request-time",
    "cancel-request",
    "decide-request",
}


def validate_command(action: str, payload: dict) -> Command:
    """Validate a transport-independent action before persisting delivery intent."""
    if action not in ACTIONS:
        raise InvalidCommand("Unknown action")
    try:
        command = Command.model_validate(payload)
    except ValidationError as exc:
        raise InvalidCommand("Invalid command fields or values") from exc
    if action in ADJUSTMENTS and (
        command.minutes is None
        or (command.minutes == 0 and action not in {"set-limit", "set-remaining"})
    ):
        raise InvalidCommand("Provide minutes from 1 to 1440 (zero is valid for set operations)")
    fields = {"expectedRevision"}
    if action in ADJUSTMENTS:
        fields.add("minutes")
    fields |= {
        "extend-access": {"until"},
        "allow-until": {"until"},
        "block": {"today", "days"},
        "request-time": {"minutes", "reason"},
        "cancel-request": {"requestId"},
        "decide-request": {"requestId", "decision", "minutes", "reason"},
    }.get(action, set())
    if command.model_fields_set - fields:
        raise InvalidCommand("Fields do not apply to this action")
    if action == "block" and command.today and command.days is not None:
        raise InvalidCommand("Choose today or days, not both")
    if action == "request-time" and command.minutes == 0:
        raise InvalidCommand("Request a positive duration")
    if command.until is not None:
        minute(command.until)
    if action in {"cancel-request", "decide-request"} and not command.requestId:
        raise InvalidCommand("A request ID is required")
    if action == "decide-request":
        if command.decision not in {
            "deny",
            "add-time",
            "grant-session",
            "unlimited-today",
            "allow-today",
        }:
            raise InvalidCommand(
                "Choose add-time, grant-session, unlimited-today, allow-today, or deny"
            )
        if command.decision in {"add-time", "grant-session"} and not command.minutes:
            raise InvalidCommand("Approval needs a positive minute amount")
    return command


class Service:
    """Coordinate policy without depending on a transport or enforcement library."""

    def __init__(
        self,
        config: AgentConfig,
        repository: StateRepository,
        enforcer: Enforcement,
        clock: Clock,
        sessions: Sessions | None = None,
    ) -> None:
        self.config, self.repository, self.enforcer, self.clock = (
            config,
            repository,
            enforcer,
            clock,
        )
        self.locks = {user: threading.RLock() for user in config.managedUsers}
        self.sessions = sessions
        self.lock_samples: dict[str, tuple[float, str, int, int]] = {}
        self.observations: dict[str, Observation] = {}
        self.errors: dict[str, str] = {}
        self.last_apply: dict[str, datetime] = {}
        from .local_usage import LocalUsage

        self.usage: LocalUsage | None = LocalUsage(self) if config.agent.backend else None

    @contextmanager
    def command_lock(self, user: str) -> Iterator[None]:
        if not self.locks[user].acquire(timeout=self.config.agent.requestTimeoutSeconds):
            raise Unavailable("A policy update is still running; retry with the same operation ID")
        try:
            yield
        finally:
            self.locks[user].release()

    def check_user(self, user: str) -> None:
        if user not in self.locks:
            raise KeyError("Unknown managed user")

    def policy(self, user: str, state: State, now: datetime) -> Policy:
        cfg = self.config.managedUsers[user]
        policy = effective_policy(
            cfg.timeWindows.for_day(now.isoweekday()),
            cfg.timeWindows.for_day(now.isoweekday(), homework=True),
            cfg.playTime.dailyLimitMinutes * 60,
            state,
            now,
        )
        if self.usage:
            policy = replace(policy, limit_seconds=self.usage.cap(user, now))
        return policy

    def status(self, user: str) -> dict:
        self.check_user(user)
        with self.command_lock(user):
            now = self.clock.now()
            state = self.repository.load(user, now)
            policy = self.policy(user, state, now)
            observation = self.observations.get(user)
            stale = observation is None or (
                now.timestamp() - observation.observed_at.timestamp()
            ) > max(10, self.config.pollIntervalSeconds * 3)
            application = self.repository.application_status(user)
            applied = (
                application.get("revision") == state.revision
                and application.get("day") == state.day
                and not application.get("error")
            )
            decision = (
                decide(policy, observation, now, self.config.warningLeadSeconds)
                if observation and not stale
                else None
            )
            cfg = self.config.managedUsers[user]
            usage_status = self.usage.status(user) if self.usage else {}
            shared = usage_status.get("shared")
            if shared and observation and not stale and observation.play_left > 0:
                fresh = (
                    shared["day"] == now.date().isoformat()
                    and 0 <= (now - datetime.fromisoformat(shared["at"])).total_seconds() <= 10
                )
                if fresh:
                    additional = max(
                        0,
                        usage_status["localUsage"]["playSeconds"] - shared["localReportedSeconds"],
                    )
                    remaining = (
                        86400
                        if shared["limitSeconds"] is None
                        else max(
                            0, shared["limitSeconds"] - shared["reportedUsedSeconds"] - additional
                        )
                    )
                    decision = decide(
                        policy,
                        replace(observation, play_left=remaining),
                        now,
                        self.config.warningLeadSeconds,
                    )
            dns = (
                cfg.dnsProfiles.homeworkProfile
                if decision and decision.homework
                else cfg.dnsProfiles.leisureProfile
                if decision and decision.remaining
                else cfg.dnsProfiles.restrictedProfile
            )
            pending = state.request if state.request and state.request.active(now) else None
            return {
                **usage_status,
                "user": user,
                "revision": state.revision,
                "phase": decision.phase if decision else "unknown",
                "connectivity": "stale" if stale else "online",
                "lastSeen": observation.observed_at.isoformat() if observation else None,
                "application": {
                    **application,
                    "status": "applied" if applied else "pending",
                    "error": self.errors.get(user) or application.get("error"),
                },
                "blocked": policy.blocked,
                "blockedUntilDate": state.blocked_until,
                "muted": state.muted,
                "pendingRequest": {**asdict(pending), "requestedAt": pending.created_at}
                if pending
                else None,
                "request": asdict(state.request) if state.request else None,
                "insideAllowedWindow": decision.inside if decision else None,
                "insideUnmeteredWindow": decision.homework if decision else None,
                "remainingPlayTimeSeconds": observation.play_left
                if observation and not stale
                else None,
                "remainingAllowedSeconds": observation.allowed_left
                if observation and not stale
                else None,
                "remainingCountdownSeconds": decision.remaining if decision else None,
                "allowedUntil": hhmm(decision.cutoff) if decision and decision.cutoff else None,
                "effectiveDailyLimitMinutes": (
                    cfg.playTime.dailyLimitMinutes * 60 + state.delta_seconds
                )
                / 60
                if not state.unlimited
                else None,
                "countedLockedSeconds": state.locked_seconds,
                "unlimitedToday": state.unlimited,
                "overrideExpiresAt": midnight_after(now).isoformat(),
                "desiredDnsProfile": dns,
                "dnsApplication": "external-unverified",
                "summary": {
                    "playTimeLeft": duration(None if state.unlimited else observation.play_left)
                    if observation and not stale
                    else "Unknown",
                    "allowedLeft": duration(decision.remaining) if decision else "Unknown",
                    "allowedUntil": hhmm(decision.cutoff) if decision and decision.cutoff else None,
                },
            }

    def reconcile(self, user: str, *, force: bool = False) -> bool:
        self.check_user(user)
        with self.locks[user]:
            now = self.clock.now()
            try:
                state = self.repository.load(user, now)
                policy = self.policy(user, state, now)
            except (Unavailable, ValueError, TypeError, KeyError):
                LOG.error("Stored policy is invalid for %s; preserving data for recovery", user)
                self.errors[user] = "Stored policy needs recovery"
                return False
            applied = self.repository.application_status(user)
            due = (
                user not in self.last_apply
                or (now.timestamp() - self.last_apply[user].timestamp())
                >= self.config.reconciliationSeconds
            )
            try:
                if self.usage:
                    self.usage.observe(user, self.enforcer.observe(user), policy)
                    policy = self.policy(user, state, now)
                if (
                    force
                    or due
                    or applied.get("revision") != state.revision
                    or applied.get("day") != state.day
                    or applied.get("error")
                ):
                    self.enforcer.validate(policy)
                    self.enforcer.apply(user, policy, now)
                    self.repository.applied(user, state.revision, now, None)
                    self.last_apply[user] = now
                self.observations[user] = self.enforcer.observe(user)
                self.account_locked(user, state, policy, now)
                if self.usage:
                    self.usage.observe(user, self.observations[user], policy)
                self.errors.pop(user, None)
                return True
            except (Unavailable, OSError, ValueError) as exc:
                LOG.warning(
                    "Reconciliation failed for %s: %s",
                    user,
                    str(exc) if isinstance(exc, Unavailable) else type(exc).__name__,
                )
                self.lock_samples.pop(user, None)
                self.errors[user] = "Enforcement unavailable; retrying"
                self.repository.applied(user, state.revision, now, self.errors[user])
                return False

    def account_locked(self, user: str, state: State, policy: Policy, now: datetime) -> None:
        """Charge measured, observed locked intervals without replaying usage mutations.

        Sample boundaries exclude restart, suspend, policy changes and unknown session
        state. Native consumption in the same interval is subtracted to avoid charging
        it twice. The durable total lowers Timekpr's absolute remaining allowance.
        """
        if not self.config.managedUsers[user].countLockedTime:
            return
        previous = self.lock_samples.pop(user, None)
        if self.sessions is None:
            raise Unavailable("Session observation is required for locked-time accounting")
        observation = self.observations[user]
        decision = decide(policy, observation, now, 0)
        if (
            policy.limit_seconds is None
            or decision.homework
            or not decision.inside
            or observation.play_left <= 0
            or not self.sessions.all_locked(user)
        ):
            return
        sampled = self.clock.monotonic()
        if previous and previous[1] == state.day and previous[2] == state.revision:
            elapsed = max(0, int(sampled - previous[0]))
            # A long observation gap is unknown, not evidence of locked usage.
            if elapsed <= max(10, self.config.pollIntervalSeconds * 3):
                native_used = max(0, previous[3] - observation.play_left)
                charge = min(observation.play_left, max(0, elapsed - native_used))
                if charge:
                    with self.repository.transaction():
                        state.locked_seconds += charge
                        state.revision += 1
                        self.repository.save(user, state)
                    self.enforcer.apply(user, self.policy(user, state, now), now)
                    self.repository.applied(user, state.revision, now, None)
                    self.observations[user] = self.enforcer.observe(user)
        self.lock_samples[user] = (
            sampled,
            state.day,
            state.revision,
            self.observations[user].play_left,
        )

    def execute(self, user: str, action: str, payload: dict, operation: str, actor: str) -> dict:
        """Commit validated intent once, attempt enforcement, and report current status.

        Args:
            user: Existing managed account identifier.
            action: Application command name, independent of the inbound adapter.
            payload: Strict action-specific fields.
            operation: Stable caller-generated identity reused for delivery retries.
            actor: Identity authenticated by the inbound adapter, retained in audit.

        Returns:
            Current status, including pending/applied state and duplicate receipt.
        """
        self.check_user(user)
        if self.usage and action not in {"request-time", "cancel-request"}:
            raise InvalidCommand("Shared allowance changes must use the central backend")
        if action not in ACTIONS:
            raise KeyError("Unknown action")
        if not operation or len(operation) > 128 or not actor or len(actor) > 128:
            raise InvalidCommand("A bounded operation ID and actor are required")
        command = validate_command(action, payload)
        fingerprint = hashlib.sha256(
            json.dumps([user, action, payload, actor], sort_keys=True).encode()
        ).hexdigest()
        with self.command_lock(user):
            now = self.clock.now()
            with self.repository.transaction():
                previous = self.repository.operation(operation)
                if previous:
                    if previous[0] != fingerprint:
                        raise Conflict("Operation ID was already used for a different command")
                else:
                    state = self.repository.load(user, now)
                    if (
                        command.expectedRevision is not None
                        and command.expectedRevision != state.revision
                    ):
                        raise Conflict("Policy changed; refresh and try again")
                    before = asdict(state)
                    state = self.transition(user, state, action, command, actor, now)
                    self.enforcer.validate(self.policy(user, state, now))
                    state.revision += 1
                    self.repository.save(user, state)
                    self.repository.record(operation, user, fingerprint, state.revision, now)
                    self.repository.audit(
                        user,
                        action,
                        {
                            "actor": actor,
                            "operationId": operation,
                            "before": before,
                            "after": asdict(state),
                        },
                        now,
                    )
            self.reconcile(user, force=not bool(previous))
            return {**self.status(user), "operationId": operation, "duplicate": bool(previous)}

    def transition(
        self, user: str, state: State, action: str, command: Command, actor: str, now: datetime
    ) -> State:
        return transition(
            self.config.managedUsers[user],
            state,
            action,
            command,
            actor,
            now,
            self.observations.get(user),
            self.config.requestCooldownSeconds,
        )


def transition(
    cfg: User,
    state: State,
    action: str,
    command: Command,
    actor: str,
    now: datetime,
    observation: Observation | None,
    request_cooldown: int = 60,
) -> State:
    """Apply a validated child command without depending on a transport or store."""
    state = replace(state, extra_windows=list(state.extra_windows))
    base = cfg.playTime.dailyLimitMinutes * 60
    amount = (command.minutes or 0) * 60
    if (
        action in {"extend-access", "reduce-access", "allow-until", "grant-session", "allow-today"}
        and state.blocked_until
    ):
        raise Conflict("Access is manually blocked; unblock first")
    if action == "add-time":
        if state.unlimited:
            raise Conflict("Today's budget is already unlimited")
        state.delta_seconds += amount
    elif action in {"deduct-time", "set-remaining"}:
        if observation is None or (now.timestamp() - observation.observed_at.timestamp()) > 10:
            raise Unavailable("Fresh usage is required; retry after the device reconnects")
        if state.unlimited:
            raise Conflict("Restore or set today's limit before adjusting remaining time")
        difference = (
            amount - observation.play_left
            if action == "set-remaining"
            else -min(amount, observation.play_left)
        )
        state.delta_seconds += difference
    elif action == "set-limit":
        state.delta_seconds, state.unlimited = amount - base, False
    elif action in {"unlimited-today", "allow-today"}:
        state.unlimited = True
        if action == "allow-today":
            state.extra_windows = [Window(0, 86400)]
            state.until, state.all_day = None, True
    elif action == "restore-today":
        state.all_day = False
        state.delta_seconds, state.unlimited, state.until, state.extra_windows = (
            0,
            False,
            None,
            [],
        )
    elif action in {"extend-access", "reduce-access", "allow-until"}:
        windows = effective_policy(
            cfg.timeWindows.for_day(now.isoweekday()),
            cfg.timeWindows.for_day(now.isoweekday(), homework=True),
            base,
            state,
            now,
        ).windows
        current_end = (
            state.until
            if state.until is not None
            else windows[-1].end
            if windows
            else seconds_of_day(now)
        )
        if command.until is not None:
            endpoint = minute(command.until) * 60
        elif action == "allow-until":
            raise InvalidCommand("An until=HH:MM cutoff is required")
        else:
            endpoint = current_end + (amount if action == "extend-access" else -amount)
        endpoint = min(86400, max(0, endpoint))
        if action != "reduce-access" and endpoint <= seconds_of_day(now):
            raise InvalidCommand("Cutoff has passed; choose a later time or grant a session now")
        state.until = endpoint
    elif action == "grant-session":
        start = seconds_of_day(now) // 60 * 60
        end = min(86400, ((seconds_of_day(now) + amount + 59) // 60) * 60)
        state.extra_windows = normalize([*state.extra_windows, Window(start, end)])
        if state.until is not None:
            state.until = max(state.until, end)
        if not state.unlimited:
            state.delta_seconds += amount
    elif action == "block":
        if not command.today and command.days is None:
            raise InvalidCommand("Provide today=true or days from 1 to 31")
        state.blocked_until = block_date(now, 1 if command.today else command.days or 1)
    elif action == "unblock":
        state.blocked_until = None
    elif action in {"mute", "unmute"}:
        state.muted = action == "mute"
    elif action == "request-time":
        if state.muted or state.blocked_until:
            raise Conflict("Requests are muted or access is manually blocked")
        if state.request and state.request.active(now):
            raise Conflict("A request is already pending")
        if (
            state.last_request_at
            and now.timestamp() - datetime.fromisoformat(state.last_request_at).timestamp()
            < request_cooldown
        ):
            raise Conflict("Please wait before requesting again")
        state.request = Request(
            str(uuid4()),
            now.isoformat(),
            midnight_after(now).isoformat(),
            minutes=command.minutes,
            reason=command.reason,
        )
        state.last_request_at = now.isoformat()
    elif action in {"cancel-request", "decide-request"}:
        request = state.request
        if request is None or not request.active(now) or request.id != command.requestId:
            raise Conflict("Request is no longer pending; refresh")
        if action == "cancel-request":
            state.request = replace(request, status="cancelled", decided_by=actor)
        elif command.decision == "deny":
            state.request = replace(
                request, status="denied", decided_by=actor, decision=command.reason
            )
        else:
            decision = command.decision
            if decision not in {"add-time", "grant-session", "unlimited-today", "allow-today"}:
                raise InvalidCommand(
                    "Choose add-time, grant-session, unlimited-today, allow-today, or deny"
                )
            if decision in {"add-time", "grant-session"} and not command.minutes:
                raise InvalidCommand("Approval needs a positive minute amount")
            state = transition(
                cfg, state, decision, command, actor, now, observation, request_cooldown
            )
            state.request = replace(request, status="approved", decided_by=actor, decision=decision)
    if base + state.delta_seconds > 86400:
        raise InvalidCommand("Today's budget cannot exceed 24 hours; choose unlimited today")
    state.delta_seconds = max(-base, state.delta_seconds)
    return state
