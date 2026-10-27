"""Account approval, scoped sessions, invitations and interface identities."""

import hashlib
import secrets
from datetime import timedelta
from pathlib import Path

from ..domain import Conflict, InvalidCommand
from ..ports import Clock
from .models import Authentication, Invitation, PasswordChange, ProfileUpdate, Registration
from .ports import Passwords, Store


class Unauthorized(InvalidCommand):
    """Credentials are absent, expired or invalid."""


class Forbidden(InvalidCommand):
    """An authenticated identity lacks permission."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Accounts:
    def __init__(self, config: Authentication, store: Store, passwords: Passwords, clock: Clock):
        self.config, self.store, self.passwords, self.clock = config, store, passwords, clock
        self.dummy_hash = passwords.hash(secrets.token_urlsafe(32))
        with store.transaction():
            if not store.get("accounts", config.bootstrapAdmin):
                # Bootstrap is one-shot; configuration changes cannot overwrite an account.
                password = (
                    Path(config.bootstrapPasswordFile).read_text().rstrip("\r\n")
                    if config.bootstrapPasswordFile
                    else None
                )
                if password is not None and not 12 <= len(password) <= 1024:
                    raise InvalidCommand("Bootstrap password must have at least 12 characters")
                store.put(
                    "accounts",
                    config.bootstrapAdmin,
                    {
                        "username": config.bootstrapAdmin,
                        "role": "admin",
                        "status": "approved",
                        "password": passwords.hash(password) if password else None,
                        "oidc": config.bootstrapOIDC.model_dump() if config.bootstrapOIDC else None,
                    },
                )
                if config.bootstrapOIDC:
                    store.put(
                        "oidc-identities",
                        digest(config.bootstrapOIDC.issuer + "#" + config.bootstrapOIDC.subject),
                        {"username": config.bootstrapAdmin},
                    )

    def audit(self, action: str, data: dict) -> None:
        self.store.event("admin", action, data, self.clock.now().isoformat())

    def account(self, username: str, *, admin: bool = False) -> dict:
        account = self.store.get("accounts", username)
        if not account or account["status"] != "approved":
            raise Forbidden("Account has no household access")
        if admin and account["role"] != "admin":
            raise Forbidden("Administrator access required")
        return account

    @staticmethod
    def public(account: dict) -> dict:
        return {
            **{key: account[key] for key in ("username", "role", "status", "oidc")},
            "displayName": account.get("displayName") or account["username"],
            "language": account.get("language"),
            "hasPassword": bool(account.get("password")),
        }

    def invite(self, invitation: Invitation, actor: str) -> str:
        self.account(actor, admin=True)
        token = secrets.token_urlsafe(32)
        with self.store.transaction():
            self.store.put(
                "invitations",
                digest(token),
                {
                    **invitation.model_dump(),
                    "expires": (self.clock.now() + timedelta(days=2)).isoformat(),
                },
            )
            self.audit("account.invited", {"actor": actor, "identity": invitation.identity})
        return token

    def profile(self, username: str, update: ProfileUpdate) -> dict:
        with self.store.transaction():
            account = self.account(username)
            if update.displayName is not None:
                if account.get("oidc"):
                    raise Forbidden("Profile name is managed by the identity provider")
                name = " ".join(update.displayName.split())
                if not name or not name.isprintable():
                    raise InvalidCommand("Enter a readable name")
                account["displayName"] = name
            account["language"] = update.language
            self.store.put("accounts", username, account)
        return self.public(account)

    def change_password(self, username: str, change: PasswordChange, credential: str) -> None:
        if not self.config.password.login:
            raise Forbidden("Password login is disabled")
        with self.store.transaction():
            account = self.account(username)
            session = self.store.get("sessions", digest(credential))
            if not session or session["username"] != username:
                raise Unauthorized("Sign in again before changing your password")
            if account.get("password"):
                if not self.passwords.verify(
                    account["password"], change.currentPassword.get_secret_value()
                ):
                    raise Unauthorized("Current password is incorrect")
            elif (
                session["method"] != "oidc"
                or session.get("createdAt", "")
                < (self.clock.now() - timedelta(minutes=15)).isoformat()
            ):
                raise Unauthorized("Sign in with your identity provider again first")
            account["password"] = self.passwords.hash(change.newPassword.get_secret_value())
            self.store.put("accounts", username, account)
            for key, other in self.store.items("sessions"):
                if other["username"] == username and key != digest(credential):
                    self.store.delete("sessions", key)
            self.audit("account.password-changed", {"actor": username})

    def set_role(self, username: str, role: str, actor: str) -> dict:
        with self.store.transaction():
            self.account(actor, admin=True)
            account = self.account(username)
            if (
                role != "admin"
                and account["role"] == "admin"
                and len(
                    [
                        a
                        for _, a in self.store.items("accounts")
                        if a["status"] == "approved" and a["role"] == "admin"
                    ]
                )
                == 1
            ):
                raise Conflict("Cannot demote the last administrator")
            account["role"] = role
            self.store.put("accounts", username, account)
            self.audit("account.role-changed", {"actor": actor, "username": username, "role": role})
        return self.public(account)

    def consume_invitation(self, token: str | None, method: str, identity: str) -> dict | None:
        if token is None:
            return None
        invitation = self.store.get("invitations", digest(token))
        if (
            not invitation
            or invitation["expires"] <= self.clock.now().isoformat()
            or invitation["method"] != method
            or invitation["identity"] != identity
        ):
            raise Forbidden("Invitation is invalid, expired or belongs to another identity")
        self.store.delete("invitations", digest(token))
        return invitation

    def register(self, registration: Registration) -> dict:
        if not self.config.password.registration:
            raise Forbidden("Password registration is disabled")
        encoded = self.passwords.hash(registration.password.get_secret_value())
        with self.store.transaction():
            if self.store.get("accounts", registration.username):
                raise Conflict("Account already exists")
            invitation = self.consume_invitation(
                registration.invitation, "password", registration.username
            )
            account = {
                "username": registration.username,
                "password": encoded,
                "oidc": None,
                "role": invitation["role"] if invitation else "parent",
                "status": "approved"
                if invitation or not self.config.requireApproval
                else "pending",
            }
            self.store.put("accounts", registration.username, account)
            self.audit("account.registered", self.public(account))
        # Registration never creates a session or bypasses the login switch.
        return self.public(account)

    def session(self, account: dict, method: str) -> str:
        self.account(account["username"])
        token = secrets.token_urlsafe(32)
        with self.store.transaction():
            self.store.put(
                "sessions",
                digest(token),
                {
                    "username": account["username"],
                    "method": method,
                    "createdAt": self.clock.now().isoformat(),
                    "expires": (
                        self.clock.now() + timedelta(seconds=self.config.sessionSeconds)
                    ).isoformat(),
                },
            )
        return token

    def login(self, username: str, password: str) -> str:
        if not self.config.password.login:
            raise Forbidden("Password login is disabled")
        account = self.store.get("accounts", username)
        valid = self.passwords.verify((account or {}).get("password") or self.dummy_hash, password)
        if not account or not account.get("password") or not valid:
            raise Unauthorized("Invalid credentials")
        return self.session(account, "password")

    def authenticate(self, token: str) -> dict:
        session = self.store.get("sessions", digest(token))
        if not session or session["expires"] <= self.clock.now().isoformat():
            raise Unauthorized("Invalid or expired session")
        if not getattr(self.config, session["method"]).login:
            raise Forbidden("This login method is disabled")
        return self.account(session["username"])

    def approve(self, username: str, role: str, actor: str) -> dict:
        with self.store.transaction():
            self.account(actor, admin=True)
            account = self.store.get("accounts", username)
            if not account:
                raise KeyError(username)
            if account["status"] != "pending":
                raise Conflict("Only pending accounts can be approved")
            account.update(status="approved", role=role)
            self.store.put("accounts", username, account)
            self.audit("account.approved", {"actor": actor, **self.public(account)})
        return self.public(account)

    def revoke(self, username: str, actor: str) -> None:
        with self.store.transaction():
            self.account(actor, admin=True)
            account = self.store.get("accounts", username)
            if not account:
                raise KeyError(username)
            admins = [
                a
                for _, a in self.store.items("accounts")
                if a["status"] == "approved" and a["role"] == "admin"
            ]
            if account in admins and len(admins) == 1:
                raise Conflict("Cannot revoke the last administrator")
            account["status"] = "revoked"
            self.store.put("accounts", username, account)
            self.audit("account.revoked", {"actor": actor, "username": username})

    def link(self, interface: str, external_id: str, username: str, actor: str) -> None:
        with self.store.transaction():
            self.account(actor, admin=True)
            self.account(username)
            existing = self.store.get("identities", f"{interface}:{external_id}")
            if existing and existing["username"] != username:
                raise Conflict("Messaging identity already belongs to another account")
            self.store.put("identities", f"{interface}:{external_id}", {"username": username})
            self.audit(
                "identity.linked", {"actor": actor, "interface": interface, "username": username}
            )

    def interface_actor(self, interface: str, external_id: str) -> dict:
        identity = self.store.get("identities", f"{interface}:{external_id}")
        if not identity:
            raise Forbidden("Messaging identity has not been linked by an administrator")
        return self.account(identity["username"])

    def link_oidc(self, issuer: str, subject: str, username: str, actor: str) -> None:
        if issuer not in {p.issuer for p in self.config.providers.values()}:
            raise InvalidCommand("OIDC issuer is not configured")
        key = digest(issuer + "#" + subject)
        with self.store.transaction():
            self.account(actor, admin=True)
            account = self.account(username)
            existing = self.store.get("oidc-identities", key)
            if existing and existing["username"] != username:
                raise Conflict("OIDC identity already belongs to another account")
            self.store.put("oidc-identities", key, {"username": username})
            account["oidc"] = {"issuer": issuer, "subject": subject}
            self.store.put("accounts", username, account)
            self.audit("identity.oidc-linked", {"actor": actor, "username": username})

    def oidc_account(
        self,
        issuer: str,
        subject: str,
        invitation_token: str | None,
        *,
        display_name: str | None = None,
    ) -> dict:
        """Resolve a stable identity and refresh its optional presentation name."""
        identity = issuer + "#" + subject
        key = digest(identity)
        with self.store.transaction():
            existing = self.store.get("oidc-identities", key)
            if existing:
                account = self.store.get("accounts", existing["username"])
                if account is None:
                    raise Unauthorized("Identity account is missing")
                if display_name and account.get("displayName") != display_name:
                    account["displayName"] = display_name
                    self.store.put("accounts", account["username"], account)
                return account
            if not self.config.oidc.registration:
                raise Forbidden("OIDC registration is disabled")
            invitation = self.consume_invitation(invitation_token, "oidc", identity)
            username = "oidc-" + key[:24]
            account = {
                "username": username,
                "displayName": display_name,
                "password": None,
                "oidc": {"issuer": issuer, "subject": subject},
                "role": invitation["role"] if invitation else "parent",
                "status": "approved"
                if invitation or not self.config.requireApproval
                else "pending",
            }
            self.store.put("accounts", username, account)
            self.store.put("oidc-identities", key, {"username": username})
            self.audit("account.registered", self.public(account))
            return account
