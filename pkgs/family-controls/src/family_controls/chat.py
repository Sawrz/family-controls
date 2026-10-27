"""Transport-neutral chat preferences, confirmations and concise command receipts."""

from datetime import timedelta
from uuid import uuid4

from .domain import InvalidCommand, Unavailable
from .ports import AgentGateway, Clock, StateRepository
from .presentation import LABELS, child_label, receipt_text, tr


class Chat:
    def __init__(self, kind: str, gateway: AgentGateway, repository: StateRepository, clock: Clock):
        self.kind, self.gateway, self.repository, self.clock = kind, gateway, repository, clock

    def record(self, key: str) -> dict:
        value = self.repository.relay_get(key)
        return value if isinstance(value, dict) else {}

    def language(self, actor: str | None = None) -> str:
        preferences = self.gateway.preferences()
        override = (
            self.gateway.authorize(actor).get("language")
            if actor
            else preferences["chatLanguages"].get(self.kind)
        )
        return override or preferences["defaultLanguage"]

    def response_language(self, actor: str | None = None) -> str:
        """Localize recovery even when the sender's account is unlinked or revoked."""
        try:
            return self.language(actor)
        except InvalidCommand:
            return self.language()

    def change_language(self, actor: str, scope: str, value: str) -> str:
        if value not in {"en", "de", "default"}:
            raise InvalidCommand("Use en, de or default")
        self.gateway.language(actor, scope, None if value == "default" else value)
        return tr(self.language(actor), "Language saved")

    def confirmation(
        self, children: list[str], action: str, body: dict, actor: str
    ) -> tuple[str, str]:
        identifier = uuid4().hex
        record = {
            "children": children,
            "action": action,
            "body": body,
            "actor": actor,
            "expires": (self.clock.now() + timedelta(minutes=10)).isoformat(),
        }
        self.repository.relay_set("chat-confirm:" + identifier, record)
        names = ", ".join(child_label(child, self.gateway.status(child)) for child in children)
        language = self.language(actor)
        text = (
            tr(
                language,
                "{names}: {action}, {minutes} minutes each today. Confirm?",
                names=names,
                action=tr(language, LABELS.get(action, action)),
                minutes=int(body["minutes"]),
            )
            if "minutes" in body
            else tr(
                language,
                "{name}: {action} today. Confirm?",
                name=names,
                action=tr(language, LABELS.get(action, action)),
            )
        )
        if action == "unlimited-today":
            text += "\n" + tr(
                language, "Only the budget is removed; allowed hours and blocks still apply."
            )
        elif action == "allow-today":
            text += "\n" + tr(
                language,
                "Budget and schedule restrictions are removed until local midnight; blocks still apply.",
            )
        return identifier, text

    def confirm(self, identifier: str, actor: str) -> str:
        record = self.repository.relay_get("chat-confirm:" + identifier)
        language = self.language(actor)
        if (
            not isinstance(record, dict)
            or record["actor"] != actor
            or record["expires"] <= self.clock.now().isoformat()
        ):
            raise InvalidCommand(tr(language, "This action expired. Open the menu again."))
        lines = []
        pending = False
        for child in record["children"]:
            try:
                result = self.gateway.command(
                    child,
                    record["action"],
                    record["body"],
                    f"chat:{self.kind}:{identifier}:{child}",
                    actor,
                )
                lines.append(self.receipt(child, result, record["action"], record["body"], actor))
            except (Unavailable, InvalidCommand, OSError) as exc:
                pending = True
                lines.append(f"{child}: {exc}")
                lines.append(
                    tr(
                        language,
                        "Some changes are still waiting. Retry this confirmation to finish.",
                    )
                )
        self.repository.relay_set("chat-confirm:" + identifier, {**record, "pending": pending})
        return "\n".join(lines)

    def receipt(self, child: str, result: dict, action: str, body: dict, actor: str) -> str:
        account = self.gateway.authorize(actor)
        operation = {
            "action": action,
            "payload": body,
            "actor": account["username"],
            "status": "queued",
            **result.get("operation", {}),
            "actorName": account.get("displayName") or account["username"],
        }
        return receipt_text(child, result, operation, self.language(actor))
