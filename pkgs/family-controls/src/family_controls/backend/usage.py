"""Transactional child-wide allowance allocation and bounded weekly reports."""

from datetime import date, timedelta

from ..domain import Conflict, InvalidCommand
from ..usage import Allocation, Usage
from .ports import Store


class UsageLedger:
    """Reserve credit before delivery; never recycle an unreachable device's credit."""

    def __init__(self, store: Store, allocation_seconds: int = 300):
        self.store, self.allocation_seconds = store, allocation_seconds

    @staticmethod
    def key(child: str, device: str, user: str, day: date) -> str:
        return f"{child}/{day.isoformat()}/{device}/{user}"

    def accept(self, child: str, device: str, report: Usage, today: date) -> None:
        """Accept monotonic progress inside the caller's synchronization transaction."""
        if report.day > today or report.day < today - timedelta(days=366):
            raise InvalidCommand("Usage day is outside the reporting interval")
        key = self.key(child, device, report.user, report.day)
        previous = self.store.get("usage", key)
        if previous and previous["epoch"] != str(report.epoch):
            raise Conflict(
                "Local accounting state was replaced; recover it before resuming this day's allowance"
            )
        if previous and report.sequence <= previous["sequence"]:
            return
        allocation = self.store.get("allocations", key) or {"issuedSeconds": 0}
        if report.issuedSeconds > allocation["issuedSeconds"]:
            raise Conflict("Device reported unissued credit")
        # A final enforcement interval can arrive after a surrender report.
        # Charge it even when usage exceeds the cap; rejecting it would wedge
        # synchronization permanently and conceal the overrun from parents.
        for field in (
            "playSeconds",
            "activeSeconds",
            "unmeteredSeconds",
            "observedSeconds",
            "unknownSeconds",
            "issuedSeconds",
            "returnedSeconds",
        ):
            if previous and getattr(report, field) < previous[field]:
                raise Conflict("Usage counters cannot move backwards")
        self.store.put(
            "usage",
            key,
            {
                **report.model_dump(mode="json"),
                "child": child,
                "device": device,
            },
        )

    def balance(self, child: str, day: date, limit: int | None) -> dict:
        rows = self.store.usage_range("usage", child, day.isoformat(), day.isoformat())
        allocations = self.store.usage_range("allocations", child, day.isoformat(), day.isoformat())
        reports = {(r["device"], r["user"]): r for r in rows}
        spent = sum(r["playSeconds"] for r in rows)
        reserved = sum(
            max(
                0,
                a["issuedSeconds"]
                - reports.get((a["device"], a["user"]), {}).get("returnedSeconds", 0)
                - reports.get((a["device"], a["user"]), {}).get("playSeconds", 0),
            )
            for a in allocations
        )
        return {
            "reportedUsedSeconds": spent,
            "reservedSeconds": reserved,
            "availableSeconds": max(0, (limit or 0) - spent - reserved),
            "limitSeconds": limit,
        }

    def allocate(
        self,
        child: str,
        device: str,
        report: Usage,
        today: date,
        limit: int | None,
        revision: int,
        *,
        blocked: bool = False,
    ) -> Allocation:
        """Top up a current device without spending credit reserved anywhere else."""
        key = self.key(child, device, report.user, today)
        row = self.store.get("allocations", key) or {
            "child": child,
            "device": device,
            "user": report.user,
            "day": today.isoformat(),
            "issuedSeconds": 0,
        }
        # Existing native usage and the enforcer's final polling interval are
        # already spent, not newly available credit. Include them in the absolute
        # device cap without resetting its native daily counter.
        row["issuedSeconds"] = max(
            row["issuedSeconds"], report.playSeconds + report.returnedSeconds
        )
        self.store.put("allocations", key, row)
        balance = self.balance(child, today, limit)
        others_active = any(
            r.get("needsTime") and r["device"] != device
            for r in self.store.usage_range("usage", child, today.isoformat(), today.isoformat())
        )
        target = (
            self.allocation_seconds
            if report.needsTime
            else 0
            if others_active and balance["availableSeconds"] == 0
            else 30
        )
        if limit is None:
            target = max(0, 86400 - report.playSeconds)
        if blocked or (
            limit is not None
            and balance["reportedUsedSeconds"] + balance["reservedSeconds"] > limit
        ):
            target = 0
        remaining = max(0, row["issuedSeconds"] - report.returnedSeconds - report.playSeconds)
        # A changed policy is acknowledged after the old allocation is surrendered.
        # This includes returning from unlimited use to a finite budget.
        if report.policyRevision == revision and not blocked:
            added = max(0, target - remaining)
            if limit is not None:
                added = min(added, balance["availableSeconds"])
            row["issuedSeconds"] += added
        self.store.put("allocations", key, row)
        return Allocation(
            day=today,
            issuedSeconds=row["issuedSeconds"],
            returnedSeconds=report.returnedSeconds,
            policyRevision=revision,
            retainSeconds=target,
            **self.balance(child, today, limit),
        )

    def week(self, child: str, start: date, today: date, assignments: list[dict]) -> dict:
        if start > today or start < today - timedelta(days=366):
            raise InvalidCommand("Choose a week within the last year")
        end = min(start + timedelta(days=6), today)
        rows = self.store.usage_range("usage", child, start.isoformat(), end.isoformat())
        days = []
        for offset in range(7):
            day = start + timedelta(days=offset)
            records = [r for r in rows if r["day"] == day.isoformat()]
            devices = [
                {
                    "device": r["device"],
                    "user": r["user"],
                    "playSeconds": r["playSeconds"],
                    "activeSeconds": r["activeSeconds"] if r["observedSeconds"] else None,
                    "unmeteredSeconds": r["unmeteredSeconds"] if r["observedSeconds"] else None,
                    "unknownSeconds": r["unknownSeconds"],
                }
                for r in records
            ]
            days.append(
                {
                    "day": day.isoformat(),
                    "availability": "future"
                    if day > today
                    else "partial"
                    if records
                    else "missing",
                    "devices": devices,
                    **{
                        field: sum(r[field] for r in devices)
                        if devices and all(r[field] is not None for r in devices)
                        else None
                        for field in (
                            "playSeconds",
                            "activeSeconds",
                            "unmeteredSeconds",
                            "unknownSeconds",
                        )
                    },
                }
            )
        return {
            "child": child,
            "start": start.isoformat(),
            "days": days,
            "assignments": assignments,
            "accounting": "sum-device-usage",
        }
