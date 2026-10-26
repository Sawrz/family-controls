"""Command-line adapter for local child requests and authenticated parent actions."""

import argparse
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4

from ..application import ACTIONS, ADJUSTMENTS
from ..config import BackendConnection, secret
from ..domain import InvalidCommand, Unavailable
from .client import BackendClient
from .http import unix_request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-path", default="/run/family-controls/control.sock")
    parser.add_argument("--backend", help="Central backend HTTPS origin")
    parser.add_argument("--token-file")
    parser.add_argument("--ca-file")
    parser.add_argument("--operation-id", help="Reuse this ID when retrying the same command")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    provision = subparsers.add_parser(
        "device-credential", help="Create a private device token; print only its SHA-256 hash"
    )
    provision.add_argument("--output-token-file", required=True)
    hash_token = subparsers.add_parser(
        "device-credential-hash", help="Print the SHA-256 hash of an existing device token"
    )
    hash_token.add_argument("--input-token-file", required=True)
    login = subparsers.add_parser("login")
    login.add_argument("--username", required=True)
    login.add_argument("--password-file", required=True)
    login.add_argument("--output-token-file", required=True)
    api = subparsers.add_parser(
        "api", help="Administrative API: enrollment, approvals and accounts"
    )
    api.add_argument("route")
    api.add_argument("--method", choices=["GET", "POST", "PUT", "DELETE"], default="GET")
    api.add_argument("--body-file")

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
        if args.command == "device-credential":
            token = secrets.token_urlsafe(32)
            descriptor = os.open(
                args.output_token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w") as output:
                output.write(token + "\n")
            result = {"credentialHash": hashlib.sha256(token.encode()).hexdigest()}
        elif args.command == "device-credential-hash":
            result = {
                "credentialHash": hashlib.sha256(secret(args.input_token_file).encode()).hexdigest()
            }
        elif args.command in {"status", "request-time", "cancel-request"}:
            result = unix_request(
                args.socket_path,
                "/v1/" + args.command,
                None if args.command == "status" else payload,
                operation,
            )
        else:
            if not args.backend or (args.command != "login" and not args.token_file):
                parser.error("Remote commands require --backend and --token-file")
            client = BackendClient(
                BackendConnection(url=args.backend, tokenFile=args.token_file, caFile=args.ca_file)
            )
            if args.command == "login":
                result = client.request(
                    "/api/v1/auth/login",
                    {
                        "username": args.username,
                        "password": Path(args.password_file).read_text().rstrip("\r\n"),
                    },
                )
                path = Path(args.output_token_file)
                with path.open("x") as output:
                    path.chmod(0o600)
                    output.write(result.pop("token"))
                result["tokenFile"] = str(path)
            elif args.command == "api":
                body = json.loads(Path(args.body_file).read_text()) if args.body_file else None
                result = client.request(
                    args.route, body, method=args.method, headers={"Idempotency-Key": operation}
                )
            else:
                action = "status" if args.command == "remote-status" else args.command
                route = f"/api/v1/children/{args.user}/" + (
                    action if action in {"status", "history"} else "commands/" + action
                )
                result = client.request(
                    route,
                    None if action in {"status", "history"} else payload,
                    headers={"Idempotency-Key": operation},
                )
        print(json.dumps(result, indent=2, sort_keys=True))
    except (InvalidCommand, Unavailable, OSError) as exc:
        print(f"{exc}\nOperation ID for a safe retry: {operation}", file=sys.stderr)
        raise SystemExit(1) from exc
