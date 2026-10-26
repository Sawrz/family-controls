"""Validated cumulative usage and allocation contracts shared by agents and server."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from .config import Identifier, Model

Seconds = Annotated[int, Field(strict=True, ge=0, le=315360000)]


class Usage(Model):
    """One device account's monotonic snapshot for one policy day.

    Sequence numbers and cumulative values make retransmission harmless. Issued
    credit belongs to the server; returned credit is acknowledged only after the
    device has durably reduced its enforceable limit.
    """

    user: Identifier
    day: date
    epoch: UUID = UUID(int=0)
    sequence: Annotated[int, Field(strict=True, ge=0)] = 0
    playSeconds: Seconds = 0
    nativeSeconds: Seconds = 0
    lockedSeconds: Seconds = 0
    observedAt: datetime | None = None
    activeSeconds: Seconds = 0
    unmeteredSeconds: Seconds = 0
    observedSeconds: Seconds = 0
    unknownSeconds: Seconds = 0
    issuedSeconds: Seconds = 0
    returnedSeconds: Seconds = 0
    policyRevision: Annotated[int, Field(strict=True, ge=0)] = 0
    needsTime: bool = False

    @model_validator(mode="after")
    def counters(self) -> "Usage":
        if self.returnedSeconds > self.issuedSeconds:
            raise ValueError("Cannot return credit that was not issued")
        if self.unmeteredSeconds > self.activeSeconds:
            raise ValueError("Unmetered activity is a subset of active computer time")
        return self


class Allocation(Model):
    day: date
    issuedSeconds: Seconds
    returnedSeconds: Seconds
    policyRevision: Annotated[int, Field(strict=True, ge=1)]
    availableSeconds: Seconds
    reportedUsedSeconds: Seconds
    reservedSeconds: Seconds
    limitSeconds: Seconds | None
    retainSeconds: Seconds = 300

    @property
    def cap(self) -> int:
        return self.issuedSeconds - self.returnedSeconds
