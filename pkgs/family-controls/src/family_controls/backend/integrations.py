"""Integration visibility and two-step, ownership-verified messaging links."""

import secrets
from datetime import datetime, timedelta

from ..domain import Conflict, InvalidCommand
from .accounts import Accounts, Forbidden, digest
from .models import (
    BackendConfig,
    IntegrationReport,
    LanguageChange,
    LanguagePreferences,
    LinkClaim,
    LinkConfirmation,
)


class Integrations:
    def __init__(self, config: BackendConfig, accounts: Accounts):
        self.config, self.accounts = config, accounts
        self.store, self.clock = accounts.store, accounts.clock

    def preferences(self) -> dict:
        return self.store.get("preferences", "language") or self.config.language.model_dump()

    def set_preferences(self, preferences: LanguagePreferences, actor: str) -> dict:
        with self.store.transaction():
            self.accounts.account(actor, admin=True)
            self.store.put("preferences", "language", preferences.model_dump())
        return self.preferences()

    def language(self, kind: str, actor: str, change: LanguageChange) -> dict:
        with self.store.transaction():
            account = self.accounts.account(actor, admin=change.scope == "chat")
            if change.scope == "me":
                account["language"] = change.language
                self.store.put("accounts", actor, account)
            else:
                preferences = self.preferences()
                if change.language is None:
                    preferences["chatLanguages"].pop(kind, None)
                else:
                    preferences["chatLanguages"][kind] = change.language
                self.store.put("preferences", "language", preferences)
        return self.preferences()

    def configured(self, kind: str) -> bool:
        return kind in {
            *self.config.localAdapters.values(),
            *(item.kind for item in self.config.interfaces.values()),
        }

    def report(self, kind: str, report: IntegrationReport) -> None:
        now = self.clock.now().isoformat()
        with self.store.transaction():
            old = self.store.get("integration-health", kind) or {}
            self.store.put(
                "integration-health",
                kind,
                {
                    **report.model_dump(),
                    "lastReport": now,
                    "lastSuccess": now if report.status == "connected" else old.get("lastSuccess"),
                },
            )

    def overview(self, username: str | None = None) -> list[dict]:
        result = []
        for kind in ("telegram", "matrix"):
            configured = self.configured(kind)
            health = self.store.get("integration-health", kind) or {}
            fresh = (
                bool(health)
                and (
                    self.clock.now() - datetime.fromisoformat(health["lastReport"])
                ).total_seconds()
                <= 90
            )
            metadata = self.config.integrationMetadata.get(kind)
            links = [
                {
                    "externalId": key.removeprefix(kind + ":"),
                    "label": value.get("label"),
                    "username": value["username"],
                    "displayName": (self.store.get("accounts", value["username"]) or {}).get(
                        "displayName"
                    )
                    or value["username"],
                }
                for key, value in self.store.items("identities")
                if key.startswith(kind + ":")
                and (username is None or value["username"] == username)
            ]
            result.append(
                {
                    "kind": kind,
                    "configured": configured,
                    "status": "not-configured"
                    if not configured
                    else health["status"]
                    if fresh
                    else "unknown",
                    "bot": health.get("bot") or (metadata.bot if metadata else None),
                    "destination": health.get("destination")
                    or (metadata.destination if metadata else None),
                    "lastSuccess": health.get("lastSuccess"),
                    "links": links,
                    "challenge": self.challenge(username, kind) if username else None,
                }
            )
        return result

    def challenge(self, username: str, kind: str) -> dict | None:
        record = self.store.get("link-challenges", f"{username}:{kind}")
        if not record or record["expires"] <= self.clock.now().isoformat():
            return None
        return {key: value for key, value in record.items() if key != "tokenHash"}

    def begin(self, username: str, kind: str) -> dict:
        self.accounts.account(username)
        if not self.configured(kind):
            raise InvalidCommand("This integration is not configured")
        token = secrets.token_urlsafe(32)
        record = {
            "challengeId": secrets.token_urlsafe(24),
            "tokenHash": digest(token),
            "expires": (self.clock.now() + timedelta(minutes=10)).isoformat(),
            "externalId": None,
            "label": None,
        }
        with self.store.transaction():
            self.store.put("link-challenges", f"{username}:{kind}", record)
        return {"token": token, **{k: v for k, v in record.items() if k != "tokenHash"}}

    def claim(self, kind: str, claim: LinkClaim) -> None:
        """Only the authenticated adapter supplies the observed sender identity."""
        supplied = digest(claim.token.get_secret_value())
        with self.store.transaction():
            for key, record in self.store.items("link-challenges"):
                if not key.endswith(":" + kind) or not secrets.compare_digest(
                    record["tokenHash"], supplied
                ):
                    continue
                if record["expires"] <= self.clock.now().isoformat():
                    break
                self.accounts.account(key.removesuffix(":" + kind))
                if record["externalId"] not in (None, claim.externalId):
                    raise Conflict("This linking request was already claimed")
                record["externalId"] = claim.externalId
                record["label"] = claim.label
                self.store.put("link-challenges", key, record)
                return
        raise InvalidCommand("Link code is invalid or expired; start again in My account")

    def confirm(self, username: str, kind: str, confirmation: LinkConfirmation) -> None:
        with self.store.transaction():
            self.accounts.account(username)
            record = self.challenge(username, kind)
            if (
                not self.configured(kind)
                or not record
                or (record["challengeId"], record["externalId"])
                != (confirmation.challengeId, confirmation.externalId)
            ):
                raise Conflict("Link request changed or expired; start again")
            key = f"{kind}:{confirmation.externalId}"
            existing = self.store.get("identities", key)
            if existing and existing["username"] != username:
                raise Conflict("Messaging account already belongs to another parent")
            self.store.put("identities", key, {"username": username, "label": record.get("label")})
            self.store.delete("link-challenges", f"{username}:{kind}")
            self.accounts.audit(
                "identity.linked",
                {
                    "actor": username,
                    "interface": kind,
                    "externalId": confirmation.externalId,
                },
            )

    def unlink(self, kind: str, external_id: str, actor: str, *, admin: bool = False) -> None:
        with self.store.transaction():
            self.accounts.account(actor, admin=admin)
            key = f"{kind}:{external_id}"
            existing = self.store.get("identities", key)
            if not existing or (not admin and existing["username"] != actor):
                raise Forbidden("Messaging account does not belong to you")
            self.store.delete("identities", key)
            self.accounts.audit(
                "identity.unlinked", {"actor": actor, "interface": kind, "externalId": external_id}
            )
