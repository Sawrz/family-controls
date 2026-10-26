"""Verified backend transport, durable SSE consumers and a shared parent gateway."""

import json
import ssl
from collections.abc import Generator

import httpx

from ..config import BackendConnection, secret
from ..domain import Unavailable
from .http import RemoteError


class BackendClient:
    def __init__(self, config: BackendConnection, credential: str | None = None) -> None:
        self.config, self.credential = config, credential
        self.context = ssl.create_default_context(cafile=config.caFile)

    def headers(self) -> dict[str, str]:
        credential = self.credential or (
            secret(self.config.tokenFile) if self.config.tokenFile else ""
        )
        return {"Authorization": "Bearer " + credential} if credential else {}

    @staticmethod
    def check(response: httpx.Response) -> None:
        if response.status_code >= 500 or response.status_code == 429:
            raise Unavailable("Backend temporarily unavailable")
        if response.status_code >= 300:
            try:
                message = response.json().get("error", "Request rejected")
            except ValueError:
                message = "Request rejected"
            raise RemoteError(message, response.status_code)

    def request(
        self,
        route: str,
        payload: dict | None = None,
        *,
        method: str | None = None,
        headers: dict | None = None,
    ) -> dict:
        try:
            with httpx.Client(verify=self.context, timeout=10, trust_env=False) as client:
                response = client.request(
                    method or ("POST" if payload is not None else "GET"),
                    self.config.url.rstrip("/") + route,
                    json=payload,
                    headers={**self.headers(), **(headers or {})},
                )
                self.check(response)
                if len(response.content) > 2 * 1024 * 1024:
                    raise Unavailable("Backend response too large")
                result = response.json()
                if not isinstance(result, dict):
                    raise Unavailable("Invalid backend response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise Unavailable("Backend connection or TLS verification failed") from exc

    def events(self, route: str, after: int) -> Generator[dict | None, None, None]:
        """Yield events and heartbeat ticks; caller persists progress after processing."""
        try:
            with (
                httpx.Client(verify=self.context, timeout=30, trust_env=False) as client,
                client.stream(
                    "GET",
                    self.config.url.rstrip("/") + route,
                    params={"after": after},
                    headers=self.headers(),
                ) as response,
            ):
                if response.status_code != 200:
                    response.read()
                    self.check(response)
                if not response.headers.get("content-type", "").startswith("text/event-stream"):
                    raise Unavailable("Backend did not return an event stream")
                data = []
                for line in response.iter_lines():
                    if len(line) > 262144:
                        raise Unavailable("Event exceeds size limit")
                    if line.startswith(":"):
                        yield None
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        yield json.loads("\n".join(data))
                        data = []
        except (httpx.HTTPError, ValueError) as exc:
            raise Unavailable("Event connection interrupted") from exc


class Gateway:
    def __init__(self, connection: BackendConnection) -> None:
        self.client = BackendClient(connection)

    def authorize(self, actor: str) -> dict:
        return self.client.request("/api/v1/me", headers={"X-Family-Actor": actor})

    def status(self, child: str) -> dict:
        return self.client.request(f"/api/v1/children/{child}/status")

    def history(self, child: str) -> list[dict]:
        return self.client.request(f"/api/v1/children/{child}/history")["events"]

    def command(self, child: str, action: str, payload: dict, operation: str, actor: str) -> dict:
        return self.client.request(
            f"/api/v1/children/{child}/commands/{action}",
            payload,
            headers={"Idempotency-Key": operation, "X-Family-Actor": actor},
        )
