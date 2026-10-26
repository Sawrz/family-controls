"""Validated process configuration. Deployment values are supplied by Nix."""

from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import InvalidCommand, Window, normalize

Positive = Annotated[int, Field(strict=True, ge=1, le=86400)]
Minutes = Annotated[int, Field(strict=True, ge=0, le=1440)]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Windows(Model):
    allowed: dict[str, list[str]] = Field(default_factory=dict)
    unmetered: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_windows(self) -> "Windows":
        for mapping in (self.allowed, self.unmetered):
            if set(mapping) - {"ALL", *map(str, range(1, 8))}:
                raise ValueError("Window keys must be ALL or ISO weekdays 1..7")
            for values in mapping.values():
                for value in values:
                    Window.parse(value)
        return self

    def for_day(self, day: int, *, homework: bool = False) -> list[Window]:
        mapping = self.unmetered if homework else self.allowed
        return normalize([Window.parse(v) for v in mapping.get(str(day), mapping.get("ALL", []))])


class PlayTime(Model):
    dailyLimitMinutes: Minutes
    activities: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def safe_activities(self) -> "PlayTime":
        if any(not value or "\n" in value or "\r" in value for value in self.activities):
            raise ValueError("Activities must be nonempty single-line process masks")
        return self


class DNS(Model):
    restrictedProfile: str = "restricted"
    homeworkProfile: str = "homework"
    leisureProfile: str = "leisure"


class User(Model):
    timeWindows: Windows = Field(default_factory=Windows)
    playTime: PlayTime
    dnsProfiles: DNS = Field(default_factory=DNS)
    trackInactive: bool = False
    countLockedTime: bool = False
    blockTTYLogin: bool = True
    lockoutAction: Literal["terminate", "suspend", "shutdown", "lock", "kill"] = "terminate"
    killSessions: bool = True

    @model_validator(mode="after")
    def accounting(self) -> "User":
        if self.countLockedTime and self.trackInactive:
            raise ValueError(
                "Choose countLockedTime or trackInactive to avoid overlapping accounting"
            )
        return self


class BackendConnection(Model):
    url: str
    tokenFile: str | None = None
    caFile: str | None = None

    @model_validator(mode="after")
    def secure_url(self) -> "BackendConnection":
        from urllib.parse import urlsplit

        url = urlsplit(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Backend URL must use HTTPS without embedded credentials")
        if url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("Backend URL must be an origin")
        return self


class AgentSettings(Model):
    stateDir: str = "/var/lib/family-controls-agent"
    socketPath: str = "/run/family-controls/control.sock"
    socketGroup: str | None = "family-controls"
    deviceId: Identifier = "device"
    backend: BackendConnection | None = None
    enrollmentTokenFile: str | None = None
    requestTimeoutSeconds: Positive = 5
    maxConnections: Annotated[int, Field(ge=1, le=128)] = 16


class TimekprSettings(Model):
    timeoutSeconds: Positive = 5


class SystemSettings(Model):
    loginctlBinary: str = "loginctl"


class AgentConfig(Model):
    timezone: str
    managedUsers: dict[Identifier, User] = Field(min_length=1)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    timekpr: TimekprSettings = Field(default_factory=TimekprSettings)
    system: SystemSettings = Field(default_factory=SystemSettings)
    pollIntervalSeconds: Positive = 2
    warningLeadSeconds: Annotated[int, Field(ge=0, le=86400)] = 300
    reconciliationSeconds: Positive = 30
    requestCooldownSeconds: Positive = 60
    auditRetentionDays: Annotated[int, Field(ge=1, le=3650)] = 365

    @model_validator(mode="after")
    def valid_timezone(self) -> "AgentConfig":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown policy timezone") from exc
        return self


class RelayConfig(Model):
    parentGroupChatId: str
    backend: BackendConnection
    children: list[Identifier] = Field(min_length=1)
    stateDir: str = "/var/lib/family-controls-telegram"
    pollIntervalSeconds: Positive = 5

    @model_validator(mode="after")
    def unique_names(self) -> "RelayConfig":
        if len({name.casefold() for name in self.children}) != len(self.children):
            raise ValueError("Child names must be unique ignoring case")
        if not self.backend.tokenFile:
            raise ValueError("Interface backend credential is required")
        return self


class MatrixConfig(Model):
    backend: BackendConnection
    children: list[Identifier] = Field(min_length=1)
    homeserver: str
    userId: str
    deviceId: str
    tokenFile: str
    roomId: str
    stateDir: str = "/var/lib/family-controls-matrix"
    encrypted: bool = True
    trustedDevices: dict[str, dict[str, str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def secure(self) -> "MatrixConfig":
        if not self.homeserver.startswith("https://") or not self.backend.tokenFile:
            raise ValueError("Matrix requires HTTPS and a backend credential")
        return self


def secret(path: str) -> str:
    value = Path(path).read_text().strip()
    if not value or any(char.isspace() for char in value):
        raise InvalidCommand("Credential must be nonempty and contain no whitespace")
    return value
