#!/usr/bin/env python3
import argparse
import json

from common import http_request, unix_request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", default="/run/family-controls/control.sock")
    parser.add_argument("--host")
    parser.add_argument("--token-file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("request-time")

    remote_status = subparsers.add_parser("remote-status")
    remote_status.add_argument("user")

    add_time = subparsers.add_parser("add-time")
    add_time.add_argument("user")
    add_time.add_argument("minutes", type=int)

    deduct_time = subparsers.add_parser("deduct-time")
    deduct_time.add_argument("user")
    deduct_time.add_argument("minutes", type=int)

    extend_access = subparsers.add_parser("extend-access")
    extend_access.add_argument("user")
    extend_access.add_argument("minutes", type=int)
    extend_access.add_argument("--until")

    reduce_access = subparsers.add_parser("reduce-access")
    reduce_access.add_argument("user")
    reduce_access.add_argument("minutes", type=int)

    mute = subparsers.add_parser("mute")
    mute.add_argument("user")

    unmute = subparsers.add_parser("unmute")
    unmute.add_argument("user")

    block = subparsers.add_parser("block")
    block.add_argument("user")
    block.add_argument("days", type=int)

    unblock = subparsers.add_parser("unblock")
    unblock.add_argument("user")

    args = parser.parse_args()

    if args.command == "status":
        payload = unix_request(args.socket_path, "GET", "/v1/status")
    elif args.command == "request-time":
        payload = unix_request(args.socket_path, "POST", "/v1/request-time", {})
    else:
        with open(args.token_file, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
        headers = {"Authorization": f"Bearer {token}"}
        if args.command == "remote-status":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/status",
                headers=headers,
                verify=False,
            )
        elif args.command == "add-time":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/add-time",
                method="POST",
                json_payload={"minutes": args.minutes},
                headers=headers,
                verify=False,
            )
        elif args.command == "deduct-time":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/deduct-time",
                method="POST",
                json_payload={"minutes": args.minutes},
                headers=headers,
                verify=False,
            )
        elif args.command == "extend-access":
            request_payload = {"minutes": args.minutes}
            if args.until is not None:
                request_payload["until"] = args.until
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/extend-access",
                method="POST",
                json_payload=request_payload,
                headers=headers,
                verify=False,
            )
        elif args.command == "reduce-access":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/reduce-access",
                method="POST",
                json_payload={"minutes": args.minutes},
                headers=headers,
                verify=False,
            )
        elif args.command == "mute":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/mute",
                method="POST",
                json_payload={},
                headers=headers,
                verify=False,
            )
        elif args.command == "unmute":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/unmute",
                method="POST",
                json_payload={},
                headers=headers,
                verify=False,
            )
        elif args.command == "block":
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/block",
                method="POST",
                json_payload={"days": args.days},
                headers=headers,
                verify=False,
            )
        else:
            payload = http_request(
                f"https://{args.host}/v1/users/{args.user}/unblock",
                method="POST",
                json_payload={},
                headers=headers,
                verify=False,
            )

    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
