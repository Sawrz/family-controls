"""Bounded HTTP transports, TLS authentication, and Unix peer authorization."""

import grp
import http.client
import http.server
import json
import logging
import os
import pwd
import socket
import socketserver
import ssl
import struct
import threading
import urllib.parse
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..application import Service
from ..domain import Conflict, InvalidCommand, Unavailable

MAX_BODY = 16384
MAX_RESPONSE = 1024 * 1024
LOG = logging.getLogger(__name__)


class RemoteError(InvalidCommand):
    def __init__(self, message: str, status: int) -> None:
        self.status = status
        super().__init__(message)


class HTTPClient:
    def __init__(self, ca_file: str | None = None, timeout: int = 5) -> None:
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=ca_file)

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port or 443
        except ValueError as exc:
            raise InvalidCommand("Invalid HTTPS endpoint") from exc
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise InvalidCommand("Only authenticated HTTPS endpoints are supported")
        data = json.dumps(payload).encode() if payload is not None else None
        connection = http.client.HTTPSConnection(
            parsed.hostname, port, timeout=self.timeout, context=self.context
        )
        route = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection.request(
                method, route, data, {"Content-Type": "application/json", **(headers or {})}
            )
            response = connection.getresponse()
            status = response.status
            raw = response.read(MAX_RESPONSE + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise Unavailable("Connection unavailable or certificate verification failed") from exc
        finally:
            connection.close()
        if len(raw) > MAX_RESPONSE:
            raise Unavailable("Response exceeds size limit")
        if 300 <= status < 400:
            raise RemoteError("Redirects are not accepted for authenticated requests", status)
        if status >= 500 or status == 429:
            raise Unavailable("Remote service temporarily unavailable")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
        except ValueError as exc:
            raise Unavailable("Service returned an invalid response") from exc
        if status >= 400:
            raise RemoteError(str(result.get("error", "Request rejected")), status)
        return result


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "family-controls/0.2"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        if (
            self.headers.get("Transfer-Encoding")
            or len(self.headers.get_all("Content-Length", [])) != 1
        ):
            raise InvalidCommand(
                "One Content-Length header is required; chunked bodies are unsupported"
            )
        raw_length = self.headers["Content-Length"]
        if not raw_length.isdecimal():
            raise InvalidCommand("Invalid Content-Length")
        length = int(raw_length)
        if length > MAX_BODY:
            raise RemoteError("Request body too large", 413)
        if self.headers.get_content_type() != "application/json":
            raise RemoteError("Use application/json", 415)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise InvalidCommand("Incomplete request body")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise InvalidCommand("Malformed JSON") from exc
        if not isinstance(value, dict):
            raise InvalidCommand("Request body must be an object")
        return value

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        server: Any = self.server
        service: Service = server.service
        try:
            _, uid, _ = struct.unpack(
                "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            try:
                user = pwd.getpwuid(uid).pw_name
            except KeyError as exc:
                raise RemoteError("Unknown local identity", 403) from exc
            if user not in service.config.managedUsers:
                raise RemoteError("Not a managed user", 403)
            allowed = {
                "/v1/status": "status",
                "/v1/request-time": "request-time",
                "/v1/cancel-request": "cancel-request",
            }
            if self.path not in allowed:
                raise KeyError("Route not found")
            action, actor = allowed[self.path], f"unix:{uid}"
            service.check_user(user)
            if method == "GET" and action == "status":
                self.send_json(service.status(user))
            elif method == "POST" and action not in {"status", "history"}:
                operation = self.headers.get("Idempotency-Key")
                if not operation:
                    raise InvalidCommand("Idempotency-Key is required")
                result = service.execute(user, action, self.body(), operation, actor)
                self.send_json(result, 200 if result["application"]["status"] == "applied" else 202)
            else:
                raise KeyError("Route not found")
        except RemoteError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except Conflict as exc:
            self.send_json({"error": str(exc)}, 409)
        except InvalidCommand as exc:
            self.send_json({"error": str(exc)}, 400)
        except KeyError:
            self.send_json({"error": "Not found"}, 404)
        except TimeoutError:
            self.send_json({"error": "Request deadline exceeded"}, 408)
        except Unavailable:
            self.send_json({"error": "Enforcement temporarily unavailable"}, 503)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            LOG.exception("Request failed")
            self.send_json({"error": "Internal service failure"}, 500)


class BoundedThreads(socketserver.ThreadingMixIn):
    daemon_threads = True
    block_on_close = False
    service: Service
    slots: threading.BoundedSemaphore

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("Only stream sockets are supported")
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("Only stream sockets are supported")
        request.settimeout(self.service.config.agent.requestTimeoutSeconds)
        try:
            super().process_request_thread(request, client_address)
        except (OSError, TimeoutError):
            request.close()
        finally:
            self.slots.release()


class UnixServer(BoundedThreads, socketserver.UnixStreamServer):
    local = True

    def __init__(self, service: Service) -> None:
        self.service = service
        settings = service.config.agent
        path = Path(settings.socketPath)
        path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if path.parent.stat().st_mode & 0o022:
            raise ValueError("Socket directory must not be group/world writable")
        if path.exists():
            if not path.is_socket():
                raise ValueError("Refusing to replace a non-socket path")
            path.unlink()
        self.slots = threading.BoundedSemaphore(settings.maxConnections)
        super().__init__(str(path), Handler)
        if settings.socketGroup:
            os.chown(path, os.getuid(), grp.getgrnam(settings.socketGroup).gr_gid)
        path.chmod(0o660)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("localhost", timeout=5)
        self.path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def unix_request(
    path: str, route: str, payload: dict | None = None, operation: str | None = None
) -> dict:
    connection = UnixConnection(path)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request(
            "POST" if body is not None else "GET",
            route,
            body,
            {"Content-Type": "application/json", "Idempotency-Key": operation or str(uuid4())},
        )
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise Unavailable("Response too large")
        result = json.loads(raw)
        if response.status >= 400:
            raise RemoteError(result.get("error", "Request failed"), response.status)
        return result
    finally:
        connection.close()
