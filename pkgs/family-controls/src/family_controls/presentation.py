"""Shared chat vocabulary and status presentation."""

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


def status_text(child: str, status: dict) -> str:
    application = status["application"]
    request = status.get("request")
    lines = [
        child,
        f"State: {status['phase'].replace('_', ' ')} · {status['connectivity']}",
        f"Play time left: {status['summary']['playTimeLeft']}",
        f"Access until: {status.get('allowedUntil') or 'outside current window'}",
        f"Today's budget: {'Unlimited' if status.get('unlimitedToday') else str(status['effectiveDailyLimitMinutes']) + 'm'}",
        f"Changes: {application['status']}",
    ]
    if application.get("error"):
        lines.append(application["error"])
    if status.get("lastSeen"):
        lines.append(f"Last seen: {status['lastSeen']}")
    if status.get("blocked"):
        lines.append(f"Manually blocked through {status['blockedUntilDate']}")
    if status.get("muted"):
        lines.append("Requests muted until a parent unmutes them")
    if request:
        lines.append(
            f"Request: {request['status']}"
            + (f" · {request['decided_by']}" if request.get("decided_by") else "")
        )
    if status.get("operation"):
        lines.append("Command: " + status["operation"]["status"])
    return "\n".join(lines)
