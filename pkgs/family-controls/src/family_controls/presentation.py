"""Shared chat vocabulary and status presentation."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

COMMANDS = {
    "addtime": "add-time",
    "deducttime": "deduct-time",
    "extendaccess": "extend-access",
    "reduceaccess": "reduce-access",
    "setlimit": "set-limit",
    "setremaining": "set-remaining",
    "session": "grant-session",
    "until": "allow-until",
    "unlimited": "unlimited-today",
    "allday": "allow-today",
    "restore": "restore-today",
    "block": "block",
    "unblock": "unblock",
    "mute": "mute",
    "unmute": "unmute",
}
LABELS = {
    "add-time": "Add time",
    "extend-access": "Extend allowed hours",
    "deduct-time": "Deduct time",
    "reduce-access": "Reduce allowed hours",
    "set-limit": "Set today's limit",
    "set-remaining": "Set time left",
    "grant-session": "Allow a session now",
    "allow-until": "Allow until",
    "unlimited-today": "Unlimited budget today",
    "allow-today": "Allow rest of day",
    "restore-today": "Restore today's defaults",
    "block": "Pause today",
    "unblock": "Unblock",
    "mute": "Mute requests",
    "unmute": "Unmute requests",
}


# English keys keep adapter call sites readable; all user-facing chat copy lives here.
TEXT = {
    "This command expired before delivery. Check !family before retrying.": "Dieser Befehl ist vor der Zustellung abgelaufen. Prüfe !family vor einem erneuten Versuch.",
    "An undelivered action expired after ten minutes. Refresh /family and try again.": "Eine nicht zugestellte Aktion ist nach zehn Minuten abgelaufen. Öffne /family und versuche es erneut.",
    "{name}: device unavailable. Its last local policy remains in effect.": "{name}: Computer nicht erreichbar. Die zuletzt bestätigten lokalen Regeln gelten weiter.",
    "Only the budget is removed; allowed hours and blocks still apply.": "Nur das Zeitbudget entfällt; Zeitfenster und Sperren gelten weiter.",
    "Budget and schedule restrictions are removed until local midnight; blocks still apply.": "Zeitbudget und Zeitfenster entfallen bis zur lokalen Mitternacht; Sperren gelten weiter.",
    "Reply to this message with !approve 15m or !deny reason.": "Antworte auf diese Nachricht mit !approve 15m oder !deny Begründung.",
    "What would you like to do?": "Was möchtest du tun?",
    "Add time": "Zeit hinzufügen",
    "Extend allowed hours": "Erlaubte Zeiten verlängern",
    "More": "Mehr",
    "Menu": "Menü",
    "Back": "Zurück",
    "Continue": "Weiter",
    "Status": "Status",
    "History": "Verlauf",
    "Schedules": "Zeitfenster",
    "Deduct time": "Zeit abziehen",
    "Pause today": "Heute pausieren",
    "Unblock": "Freigeben",
    "Unlimited budget today": "Heute unbegrenztes Zeitbudget",
    "Allow rest of day": "Rest des Tages erlauben",
    "Restore defaults": "Vorgaben wiederherstellen",
    "Set today's limit": "Tagesbudget festlegen",
    "Set time left": "Restzeit festlegen",
    "Allow a session now": "Jetzt eine Sitzung erlauben",
    "Allow until": "Erlauben bis",
    "Mute requests": "Anfragen stummschalten",
    "Unmute requests": "Anfragen aktivieren",
    "Language": "Sprache",
    "My language": "Meine Sprache",
    "Group language": "Gruppensprache",
    "Household default": "Haushaltsvorgabe",
    "Language saved": "Sprache gespeichert",
    "Choose children": "Kinder auswählen",
    "All children": "Alle Kinder",
    "Choose minutes": "Minuten auswählen",
    "Custom…": "Eigene Dauer…",
    "Confirm": "Bestätigen",
    "Cancel": "Abbrechen",
    "Deny": "Ablehnen",
    "Deny with reason…": "Mit Begründung ablehnen…",
    "Approve {minutes} min": "{minutes} Min. genehmigen",
    "{names}: {action}, {minutes} minutes each today. Confirm?": "{names}: {action}, heute jeweils {minutes} Minuten. Bestätigen?",
    "{name}: {action} today. Confirm?": "{name}: heute {action}. Bestätigen?",
    "{name} requests {minutes} minutes": "{name} möchte {minutes} Minuten mehr",
    "{name}: request {status}": "{name}: Anfrage {status}",
    "Review remaining requests": "Weitere Anfragen ansehen",
    "{name}: {action}{amount} by {actor}.": "{name}: {action}{amount} von {actor}.",
    "approved": "genehmigt",
    "denied": "abgelehnt",
    "cancelled": "zurückgezogen",
    "expired": "abgelaufen",
    "pending": "offen",
    "queued": "vorgemerkt",
    "Waiting for the computer.": "Warten auf den Computer.",
    "Applied on all computers.": "Auf allen Computern angewendet.",
    "Could not apply the change. Check device status.": "Änderung nicht angewendet. Gerätestatus prüfen.",
    "Expired before all computers confirmed.": "Vor der Bestätigung aller Computer abgelaufen.",
    "Play time left": "Verbleibende Bildschirmzeit",
    "Today's budget": "Heutiges Zeitbudget",
    "Regular hours today": "Reguläre Zeiten heute",
    "Allowed hours today": "Heute erlaubte Zeiten",
    "Unmetered hours": "Zeiten ohne Verbrauch",
    "No allowed hours": "Keine erlaubten Zeiten",
    "Schedule unavailable": "Zeitfenster nicht verfügbar",
    "Unlimited": "Unbegrenzt",
    "Unknown": "Unbekannt",
    "Less than a minute": "Weniger als eine Minute",
    "Today": "Heute",
    "Yesterday": "Gestern",
    "Never seen": "Noch kein Kontakt",
    "Last contact": "Letzter Kontakt",
    "online": "verbunden",
    "offline": "nicht verbunden",
    "Blocked today": "Heute gesperrt",
    "Requests muted": "Anfragen stummgeschaltet",
    "No recent changes": "Keine kürzlichen Änderungen",
    "Reply with minutes, 25m, or 1h 10m. /cancel cancels.": "Antworte mit Minuten, 25m oder 1h 10m. /cancel bricht ab.",
    "Reply with HH:MM. /cancel cancels.": "Antworte mit HH:MM. /cancel bricht ab.",
    "Reply with a reason. /cancel cancels.": "Antworte mit einer Begründung. /cancel bricht ab.",
    "Prompt cancelled.": "Eingabe abgebrochen.",
    "This menu belongs to another parent. Open /family.": "Dieses Menü gehört einem anderen Elternteil. Öffne /family.",
    "This action expired. Open the menu again.": "Diese Aktion ist abgelaufen. Öffne das Menü erneut.",
    "Select at least one child.": "Wähle mindestens ein Kind aus.",
    "Some changes are still waiting. Retry this confirmation to finish.": "Einige Änderungen warten noch. Bestätige erneut, um sie abzuschließen.",
    "Return to My account and confirm the messaging account.": "Kehre zu Mein Konto zurück und bestätige das Nachrichtenkonto.",
    "Link code is invalid or expired. Start again in My account.": "Verknüpfungscode ungültig oder abgelaufen. Beginne erneut in Mein Konto.",
    "Reply to your prompt within ten minutes, or open the menu again.": "Antworte innerhalb von zehn Minuten auf deine Eingabe oder öffne das Menü erneut.",
    "Not authorized": "Nicht berechtigt",
}


def tr(language: str, text: str, **values: object) -> str:
    return (TEXT.get(text, text) if language == "de" else text).format(**values)


def child_label(child: str, status: dict) -> str:
    return status.get("displayName") or child[:1].upper() + child[1:]


def minutes_text(seconds: float | None, language: str) -> str:
    if seconds is None:
        return tr(language, "Unknown")
    if 0 < seconds < 60:
        return tr(language, "Less than a minute")
    return f"{int(max(0, seconds) // 60)} min"


def timestamp(
    value: str | None, language: str, timezone: str = "UTC", now: datetime | None = None
) -> str:
    if not value:
        return tr(language, "Never seen")
    zone = ZoneInfo(timezone)
    moment = datetime.fromisoformat(value).astimezone(zone)
    today = (now or datetime.now(zone)).astimezone(zone).date()
    label = (
        tr(language, "Today")
        if moment.date() == today
        else tr(language, "Yesterday")
        if moment.date() == today - timedelta(days=1)
        else moment.strftime("%d.%m.%Y" if language == "de" else "%Y-%m-%d")
    )
    return f"{label}, {moment:%H:%M}"


def status_text(child: str, status: dict, language: str = "en", now: datetime | None = None) -> str:
    def t(text: str) -> str:
        return tr(language, text)

    budget = status.get("effectiveDailyLimitMinutes")
    unlimited = status.get("unlimitedToday") or budget is None
    lines = [
        child_label(child, status),
        f"{t('Play time left')}: {t('Unlimited') if unlimited else minutes_text(status.get('remainingPlayTimeSeconds'), language)}",
        f"{t("Today's budget")}: {t('Unlimited') if unlimited else minutes_text((budget or 0) * 60, language)}",
    ]
    schedule = status.get("schedule")
    if schedule:
        lines.append(
            f"{t('Regular hours today')}: {', '.join(schedule['regular']) or t('No allowed hours')}"
        )
        if schedule["today"] != schedule["regular"]:
            lines.append(
                f"{t('Allowed hours today')}: {', '.join(schedule['today']) or t('No allowed hours')}"
            )
        if schedule["unmetered"]:
            lines.append(f"{t('Unmetered hours')}: {', '.join(schedule['unmetered'])}")
    else:
        lines.append(t("Schedule unavailable"))
    for device in status.get(
        "devices",
        [
            {
                "device": "",
                "lastSeen": status.get("lastSeen"),
                "connectivity": status.get("connectivity", "offline"),
            }
        ],
    ):
        lines.append(
            f"{device['device'] + ': ' if device['device'] else ''}{t(device['connectivity'])} · {t('Last contact')}: {timestamp(device.get('lastSeen'), language, status.get('timezone', 'UTC'), now)}"
        )
    if status.get("blocked"):
        lines.append(t("Blocked today"))
    if status.get("muted"):
        lines.append(t("Requests muted"))
    return "\n".join(lines)


def request_text(child: str, status: dict, request: dict, language: str) -> str:
    if request["status"] == "pending":
        text = tr(
            language,
            "{name} requests {minutes} minutes",
            name=child_label(child, status),
            minutes=request.get("minutes") or 15,
        )
    else:
        text = tr(
            language,
            "{name}: request {status}",
            name=child_label(child, status),
            status=tr(language, request["status"]),
        )
    reason = (
        request.get("reason")
        if request["status"] == "pending"
        else request.get("decision")
        if request["status"] == "denied"
        else None
    )
    return text + ("\n" + reason if reason else "")


def receipt_text(child: str, status: dict, operation: dict, language: str) -> str:
    payload = operation.get("payload", {})
    decision = payload.get("decision")
    action = (
        "denied"
        if decision == "deny"
        else "approved"
        if operation["action"] == "decide-request"
        else LABELS.get(operation["action"], operation["action"])
    )
    amount = f" · {int(payload['minutes'])} min" if "minutes" in payload else ""
    text = tr(
        language,
        "{name}: {action}{amount} by {actor}.",
        name=child_label(child, status),
        action=tr(language, action),
        amount=amount,
        actor=operation.get("actorName") or operation["actor"],
    )
    if decision == "deny":
        return text + ("\n" + payload["reason"] if payload.get("reason") else "")
    state = "failed" if operation.get("error") else operation.get("status", "queued")
    return (
        text
        + " "
        + tr(
            language,
            {
                "applied": "Applied on all computers.",
                "failed": "Could not apply the change. Check device status.",
                "expired": "Expired before all computers confirmed.",
            }.get(state, "Waiting for the computer."),
        )
    )


def error_text(language: str, message: str) -> str:
    """Keep recovery instructions localized when a backend rejects an action."""
    if language == "en" or message in TEXT:
        return tr(language, message)
    if message in TEXT.values():
        return message
    return "Aktion nicht ausgeführt. Prüfe den aktuellen Status und starte die Aktion erneut."
