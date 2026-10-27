"""Validated backend configuration and wire contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from ..config import Identifier, Minutes, Model, User, Windows
from ..usage import Usage

Role = Literal["admin", "parent"]
Key = Annotated[str, Field(min_length=1, max_length=128)]


class Method(Model):
    registration: bool = False
    login: bool = True


class OIDCProvider(Model):
    issuer: str
    clientId: str
    clientSecretFile: str
    label: str | None = Field(default=None, min_length=1, max_length=100)

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


class Assignment(Model):
    device: Identifier
    user: Identifier


class ChildPolicy(Model):
    assignments: list[Assignment] = Field(default_factory=list, max_length=32)
    timezone: str = "Europe/Berlin"
    policy: User
    displayName: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("displayName")
    @classmethod
    def readable_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value or not value.isprintable():
            raise ValueError("Enter a readable name")
        return value

    @model_validator(mode="after")
    def timezone_exists(self) -> "ChildPolicy":
        from zoneinfo import ZoneInfo

        ZoneInfo(self.timezone)
        if len({a.device for a in self.assignments}) != len(self.assignments):
            raise ValueError("A child has one OS account per assigned device")
        return self


def restricted_policy() -> User:
    """No access before parent setup; meter all user processes once configured."""
    return User.model_validate({"playTime": {"dailyLimitMinutes": 0, "activities": [".*"]}})


class ChildConfiguration(ChildPolicy):
    policy: User = Field(default_factory=restricted_policy)


class ChildSetup(Model):
    dailyLimitMinutes: Minutes
    timeWindows: Windows


class IntegrationMetadata(Model):
    bot: str | None = Field(default=None, max_length=256)
    destination: str | None = Field(default=None, max_length=256)


class IntegrationReport(IntegrationMetadata):
    status: Literal["connected", "problem"]


class LinkClaim(Model):
    token: SecretStr = Field(min_length=32, max_length=128)
    externalId: Key
    label: str | None = Field(default=None, max_length=256)


class LinkConfirmation(Model):
    challengeId: Key
    externalId: Key


class ProfileUpdate(Model):
    displayName: str | None = Field(default=None, min_length=1, max_length=256)
    language: Literal["en", "de"] | None


class LanguagePreferences(Model):
    defaultLanguage: Literal["en", "de"] = "en"
    chatLanguages: dict[Literal["telegram", "matrix"], Literal["en", "de"]] = Field(
        default_factory=dict
    )


class LanguageChange(Model):
    scope: Literal["me", "chat"]
    language: Literal["en", "de"] | None


class PasswordChange(Model):
    currentPassword: SecretStr = Field(default=SecretStr(""), max_length=1024)
    newPassword: SecretStr = Field(min_length=12, max_length=1024)


class InterfaceCredential(Model):
    kind: Literal["telegram", "matrix"]
    tokenFile: str


class TrustedDevice(Model):
    """Public device trust; private bearer tokens never belong in this configuration."""

    credentialHash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    accounts: dict[Identifier, Identifier] = Field(min_length=1, max_length=100)


class BackendConfig(Model):
    stateDir: str = "/var/lib/family-controls-backend"
    publicUrl: str
    bindHost: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 18450
    tlsCertFile: str | None = None
    tlsKeyFile: str | None = None
    authentication: Authentication
    children: dict[Identifier, ChildConfiguration] = Field(min_length=1)
    trustedDevices: dict[Identifier, TrustedDevice] = Field(default_factory=dict)
    interfaces: dict[Identifier, InterfaceCredential] = Field(default_factory=dict)
    staleSeconds: Annotated[int, Field(ge=10, le=300)] = 30
    allocationSeconds: Annotated[int, Field(ge=60, le=3600)] = 300
    adapterSocketPath: str | None = None
    adapterSocketGroup: str = "family-controls-adapters"
    localAdapters: dict[Identifier, Literal["telegram", "matrix"]] = Field(default_factory=dict)
    language: LanguagePreferences = Field(default_factory=LanguagePreferences)
    integrationMetadata: dict[Literal["telegram", "matrix"], IntegrationMetadata] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def topology(self) -> "BackendConfig":
        """Derive assignments from trusted hosts and validate the household topology."""
        from urllib.parse import urlsplit

        url = urlsplit(self.publicUrl)
        if url.scheme != "https" or not url.hostname or url.path not in {"", "/"}:
            raise ValueError("publicUrl must be an HTTPS origin")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("publicUrl must not contain credentials, query or fragment")
        hashes = [trust.credentialHash for trust in self.trustedDevices.values()]
        if len(set(hashes)) != len(hashes):
            raise ValueError("Each trusted device requires its own credential")
        assignments = {name: list(child.assignments) for name, child in self.children.items()}
        for device, trust in self.trustedDevices.items():
            expected = set(trust.accounts.items())
            existing = {
                (assignment.user, child)
                for child, entries in assignments.items()
                for assignment in entries
                if assignment.device == device
            }
            if existing and existing != expected:
                raise ValueError("Trusted device accounts conflict with child assignments")
            for user, child in trust.accounts.items():
                if child not in assignments:
                    raise ValueError("Trusted device references an unknown child")
                if not existing:
                    assignments[child].append(Assignment(device=device, user=user))
        self.children = {
            name: ChildConfiguration.model_validate(
                {**child.model_dump(exclude_unset=True), "assignments": assignments[name]}
            )
            for name, child in self.children.items()
        }
        pairs = [(a.device, a.user) for child in self.children.values() for a in child.assignments]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Each device/user must have exactly one child identity")
        for device in {a.device for c in self.children.values() for a in c.assignments}:
            if (
                len(
                    {
                        c.timezone
                        for c in self.children.values()
                        if any(a.device == device for a in c.assignments)
                    }
                )
                != 1
            ):
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
    usage: list[Usage] = Field(default_factory=list, max_length=100)


class Event(Model):
    id: int
    kind: str
    audience: str
    data: dict
    createdAt: datetime
