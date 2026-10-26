"""Durable local counters and credit enforcement for a centrally managed child."""

from dataclasses import asdict, replace
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from .config import User
from .domain import Observation, Policy, Unavailable, state_from_dict
from .usage import Allocation, Usage

if TYPE_CHECKING:
    from .application import Service


class LocalUsage:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.samples: dict[str, tuple[float, datetime, bool | None]] = {}
        self.fractions: dict[tuple[str, str, str], float] = {}

    def read(self, user: str, day: date | None = None) -> Usage:
        day = day or self.service.clock.now().date()
        value = self.service.repository.relay_get(f"usage/{user}/{day}")
        return Usage.model_validate(value) if value else Usage(user=user, day=day, epoch=uuid4())

    def save(self, value: Usage) -> None:
        self.service.repository.relay_set(
            f"usage/{value.user}/{value.day}", value.model_dump(mode="json")
        )

    def cap(self, user: str, now: datetime) -> int:
        usage = self.read(user, now.date())
        return max(
            0, usage.issuedSeconds - usage.returnedSeconds - usage.playSeconds + usage.nativeSeconds
        )

    def observe(self, user: str, observation: Observation, policy: Policy) -> None:
        """Sample known intervals; restarts, suspend and long gaps remain unknown."""
        now, clock = self.service.clock.now(), self.service.clock
        value = self.read(user)
        native = observation.raw.get("playSeconds")
        if not isinstance(native, int) or isinstance(native, bool) or native < 0:
            raise Unavailable("Enforcer does not provide cumulative usage")
        state = self.service.repository.load(user, now)
        value.playSeconds += max(0, native - value.nativeSeconds) + max(
            0, state.locked_seconds - value.lockedSeconds
        )
        value.nativeSeconds, value.lockedSeconds = native, state.locked_seconds
        value.needsTime = bool(observation.raw.get("activeActivities"))
        if self.service.config.managedUsers[user].countLockedTime and self.service.sessions:
            value.needsTime = value.needsTime or self.service.sessions.all_locked(user)
        sessions = self.service.sessions
        active = sessions.active(user) if sessions else None
        previous = self.samples.get(user)
        if previous:
            elapsed = max(0.0, clock.monotonic() - previous[0])
            wall = max(0.0, now.timestamp() - previous[1].timestamp())
            if previous[1].date() == now.date():
                known = (
                    elapsed <= max(10, self.service.config.pollIntervalSeconds * 3)
                    and abs(wall - elapsed) <= 2
                    and active is not None
                    and previous[2] is not None
                )
                if known:
                    self.add(value, "observedSeconds", elapsed)
                    if active and previous[2]:
                        self.add(value, "activeSeconds", elapsed)
                        second = now.hour * 3600 + now.minute * 60 + now.second
                        if any(w.contains(second) for w in policy.homework):
                            self.add(value, "unmeteredSeconds", elapsed)
                else:
                    self.add(value, "unknownSeconds", min(wall, 86400))
        elif value.observedAt:
            self.add(
                value,
                "unknownSeconds",
                max(0, min(86400, (now - value.observedAt).total_seconds())),
            )
        value.observedAt = now
        self.samples[user] = (clock.monotonic(), now, active)
        value.sequence += 1
        self.save(value)

    def add(self, value: Usage, field: str, seconds: float) -> None:
        key = (value.user, value.day.isoformat(), field)
        total = seconds + self.fractions.get(key, 0.0)
        whole = int(round(total, 9))
        self.fractions[key] = max(0.0, total - whole)
        setattr(value, field, getattr(value, field) + whole)

    def reports(self) -> list[dict]:
        """Resend unacknowledged snapshots, including completed offline days."""
        rows = self.service.repository.relay_items("usage/")
        result = []
        for _, row in rows:
            ack = self.service.repository.relay_get(f"usage-ack/{row['user']}/{row['day']}", -1)
            if row["sequence"] > ack or row["day"] == self.service.clock.now().date().isoformat():
                result.append(row)
        return result[:100]

    def accept(self, user: str, policy: dict, reported_request: dict | None = None) -> None:
        """Apply a server policy and durably surrender old credit before reporting it."""
        service = self.service
        allocation = Allocation.model_validate(policy["allocation"])
        now = service.clock.now()
        if allocation.day != now.date():
            raise Unavailable("Server and device policy days differ")
        with service.command_lock(user):
            value = self.read(user)
            if allocation.issuedSeconds < value.issuedSeconds:
                raise Unavailable("Server allocation moved backwards")
            if allocation.returnedSeconds > value.returnedSeconds:
                raise Unavailable("Server returned credit without a local surrender")
            service.config.managedUsers[user] = User.model_validate(policy["policy"])
            state = state_from_dict(policy["state"]).today(now)
            local_state = service.repository.load(user, now)
            local_request = local_state.request
            if local_request and asdict(local_request) != reported_request:
                # A child may submit/cancel while the HTTP exchange is in flight.
                # Preserve that unsent intent without undoing a parent decision.
                new_request = local_request.active(now) and (
                    state.request is None
                    or (state.request.id != local_request.id and not state.request.active(now))
                )
                cancellation = (
                    local_request.status == "cancelled"
                    and state.request is not None
                    and state.request.id == local_request.id
                    and state.request.status == "pending"
                )
                if (new_request or cancellation) and not state.muted and not state.blocked_until:
                    state.request = local_request
            state.locked_seconds = local_state.locked_seconds
            state.revision = max(local_state.revision + 1, policy["revision"])
            with service.repository.transaction():
                service.repository.save(user, state)
            newly_issued = allocation.issuedSeconds - value.issuedSeconds
            value.issuedSeconds = allocation.issuedSeconds
            recall = allocation.limitSeconds is not None and (
                allocation.reportedUsedSeconds + allocation.reservedSeconds
                > allocation.limitSeconds
            )
            retain = 0 if recall or state.blocked_until else allocation.retainSeconds
            unused = max(0, value.issuedSeconds - value.returnedSeconds - value.playSeconds)
            # The consumed-time counter can lag live enforcement. Surrender only
            # credit still reported spendable, plus new credit not yet installed;
            # keep already consumed but unreported time reserved until it arrives.
            try:
                observation = service.enforcer.observe(user)
                spendable = min(unused, max(0, observation.play_left) + newly_issued)
            except Unavailable:
                # Keep every reservation until enforcement can confirm a return.
                spendable = 0
            surrendered = max(0, spendable - retain)
            if surrendered:
                baseline = service.policy(user, state, now)
                service.enforcer.apply(
                    user,
                    replace(
                        baseline, limit_seconds=max(0, value.nativeSeconds + unused - surrendered)
                    ),
                    now,
                )
                value.returnedSeconds += surrendered
            value.policyRevision = policy["revision"]
            value.sequence += 1
            self.save(value)
            service.repository.relay_set(
                "shared-status/" + user,
                {
                    **allocation.model_dump(mode="json"),
                    "at": now.isoformat(),
                    "child": policy.get("child", user),
                    "localReportedSeconds": value.playSeconds,
                },
            )
            service.reconcile(user, force=True)

    def status(self, user: str) -> dict:
        value = self.read(user)
        shared = self.service.repository.relay_get("shared-status/" + user)
        return {"localUsage": value.model_dump(mode="json"), "shared": shared}
