"""Validated backend configuration and wire contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator

from ..config import Identifier, Model, User

Role = Literal["admin", "parent"]
Key = Annotated[str, Field(min_length=1, max_length=128)]


class Method(Model):
    registration: bool = False
    login: bool = True


class OIDCProvider(Model):
    issuer: str
    clientId: str
    clientSecretFile: str

    @model_validator(mode="after")
    def secure(self) -> "OIDCProvider":
        from urllib.parse import urlsplit

        url = urlsplit(self.issuer)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("OIDC requires HTTPS")
        return self


class OIDCIdentity(Model):
    issuer: str
    subject: Key


class Authentication(Model):
    password: Method = Field(default_factory=Method)
    oidc: Method = Field(default_factory=Method)
    requireApproval: bool = True
    providers: dict[Identifier, OIDCProvider] = Field(default_factory=dict)
    bootstrapAdmin: Identifier = "admin"
    bootstrapPasswordFile: str | None = None
    bootstrapOIDC: OIDCIdentity | None = None
    sessionSeconds: Annotated[int, Field(ge=300, le=86400)] = 28800

    @model_validator(mode="after")
    def bootstrap(self) -> "Authentication":
        if self.bootstrapPasswordFile is None and self.bootstrapOIDC is None:
            raise ValueError("Provision a bootstrap password or explicit OIDC identity")
        if self.bootstrapOIDC and self.bootstrapOIDC.issuer not in {
            p.issuer for p in self.providers.values()
        }:
            raise ValueError("Bootstrap OIDC identity must use a configured provider")
        return self


class ChildPolicy(Model):
    device: Identifier
    user: Identifier
    timezone: str = "Europe/Berlin"
    policy: User

    @model_validator(mode="after")
    def timezone_exists(self) -> "ChildPolicy":
        from zoneinfo import ZoneInfo

        ZoneInfo(self.timezone)
        return self


class InterfaceCredential(Model):
    kind: Literal["telegram", "matrix"]
    tokenFile: str


class BackendConfig(Model):
    stateDir: str = "/var/lib/family-controls-backend"
    publicUrl: str
    bindHost: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 18450
    tlsCertFile: str | None = None
    tlsKeyFile: str | None = None
    authentication: Authentication
    children: dict[Identifier, ChildPolicy] = Field(min_length=1)
    interfaces: dict[Identifier, InterfaceCredential] = Field(default_factory=dict)
    commandLifetimeSeconds: Annotated[int, Field(ge=30, le=3600)] = 600
    staleSeconds: Annotated[int, Field(ge=10, le=300)] = 30

    @model_validator(mode="after")
    def topology(self) -> "BackendConfig":
        from urllib.parse import urlsplit

        url = urlsplit(self.publicUrl)
        if url.scheme != "https" or not url.hostname or url.path not in {"", "/"}:
            raise ValueError("publicUrl must be an HTTPS origin")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("publicUrl must not contain credentials, query or fragment")
        pairs = [(child.device, child.user) for child in self.children.values()]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Each device/user must have exactly one child identity")
        for device in {child.device for child in self.children.values()}:
            if len({c.timezone for c in self.children.values() if c.device == device}) != 1:
                raise ValueError("Children on one device must share its policy timezone")
        return self


class Registration(Model):
    username: Identifier
    password: SecretStr = Field(min_length=12, max_length=1024)
    invitation: str | None = Field(default=None, max_length=256)


class Login(Model):
    username: Identifier
    password: SecretStr = Field(max_length=1024)


class Invitation(Model):
    identity: Key
    method: Literal["password", "oidc"] = "password"
    role: Role = "parent"


class Approval(Model):
    role: Role = "parent"


class IdentityLink(Model):
    interface: Literal["telegram", "matrix"]
    externalId: Key
    account: Identifier


class OIDCLink(OIDCIdentity):
    account: Identifier


class Enrollment(Model):
    device: Identifier
    token: SecretStr
    credential: SecretStr = Field(min_length=32, max_length=256)


class Outcome(Model):
    operationId: Key
    status: Literal["accepted", "applied", "superseded", "failed", "expired"]
    error: str | None = Field(default=None, max_length=300)


class DeviceReport(Model):
    policyRevision: Annotated[int, Field(ge=0)]
    statuses: dict[Identifier, dict] = Field(default_factory=dict, max_length=100)
    outcomes: list[Outcome] = Field(default_factory=list, max_length=100)


class Event(Model):
    id: int
    kind: str
    audience: str
    data: dict
    createdAt: datetime
