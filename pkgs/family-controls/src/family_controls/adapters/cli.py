"""Command-line adapter for local child requests and authenticated parent actions."""

import argparse
import json
import sys
from uuid import uuid4

from ..application import ACTIONS, ADJUSTMENTS
from ..config import secret
from ..domain import InvalidCommand, Unavailable
from .http import HTTPClient, unix_request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-path", default="/run/family-controls/control.sock")
    parser.add_argument("--host", help="Agent hostname:port; HTTPS certificate must match")
    parser.add_argument("--token-file")
    parser.add_argument("--ca-file")
    parser.add_argument("--operation-id", help="Reuse this ID when retrying the same command")
    parser.add_argument("--actor", default="cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    for action in sorted(ACTIONS | {"remote-status", "history"}):
        sub = subparsers.add_parser(action)
        if action not in {"request-time", "cancel-request"}:
            sub.add_argument("user")
        if action in ADJUSTMENTS:
            sub.add_argument("minutes", type=int)
        if action in {"extend-access", "allow-until"}:
            sub.add_argument("--until", required=action == "allow-until")
        if action == "block":
            sub.add_argument("days", type=int)
        if action in {"request-time", "decide-request"}:
            sub.add_argument("--reason", default="")
        if action == "request-time":
            sub.add_argument("--minutes", type=int)
        if action in {"decide-request", "cancel-request"}:
            sub.add_argument("--request-id", required=True)
        if action == "decide-request":
            sub.add_argument(
                "--decision",
                choices=["deny", "add-time", "grant-session", "unlimited-today", "allow-today"],
                required=True,
            )
            sub.add_argument("--minutes", type=int)
    args = parser.parse_args()
    payload = {
        name: getattr(args, name)
        for name in ("minutes", "until", "days", "reason", "decision")
        if getattr(args, name, None) is not None
    }
    if getattr(args, "request_id", None):
        payload["requestId"] = args.request_id
    operation = args.operation_id or str(uuid4())
    try:
        if args.command in {"status", "request-time", "cancel-request"}:
            result = unix_request(
                args.socket_path,
                "/v1/" + args.command,
                None if args.command == "status" else payload,
                operation,
            )
        else:
            if not args.host or not args.token_file:
                parser.error("Remote commands require --host and --token-file")
            action = "status" if args.command == "remote-status" else args.command
            client = HTTPClient(args.ca_file)
            result = client.request(
                f"https://{args.host}/v1/users/{args.user}/{action}",
                method="GET" if action in {"status", "history"} else "POST",
                payload=None if action in {"status", "history"} else payload,
                headers={
                    "Authorization": f"Bearer {secret(args.token_file)}",
                    "Idempotency-Key": operation,
                    "X-Family-Actor": args.actor,
                },
            )
        print(json.dumps(result, indent=2, sort_keys=True))
    except (InvalidCommand, Unavailable, OSError) as exc:
        print(f"{exc}\nOperation ID for a safe retry: {operation}", file=sys.stderr)
        raise SystemExit(1) from exc
