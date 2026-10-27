"""FastAPI inbound adapter for parent, interface and approved device operations."""

import asyncio
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from ..application import Command
from ..backend.accounts import Accounts, Forbidden, Unauthorized, digest
from ..backend.integrations import Integrations
from ..backend.models import (
    Approval,
    ChildPolicy,
    ChildSetup,
    DeviceReport,
    Enrollment,
    IdentityLink,
    IntegrationReport,
    Invitation,
    LanguageChange,
    LanguagePreferences,
    LinkClaim,
    LinkConfirmation,
    Login,
    OIDCLink,
    PasswordChange,
    ProfileUpdate,
    Registration,
)
from ..backend.service import Backend
from ..config import secret
from ..domain import Conflict, InvalidCommand, Unavailable
from .oidc import OIDC

LOG = logging.getLogger(__name__)


def create_app(backend: Backend, accounts: Accounts, oidc: OIDC) -> FastAPI:
    """Compose HTTP routes without moving application rules into request handlers."""
    app = FastAPI(title="Family Controls", version="1.0", docs_url=None, redoc_url=None)
    attempts: dict[str, deque] = defaultdict(deque)
    config = backend.config
    integrations = Integrations(config, accounts)

    @app.exception_handler(RequestValidationError)
    async def validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default includes submitted input, which may contain passwords/tokens.
        return JSONResponse(
            {
                "error": "Invalid request",
                "fields": [{"location": e["loc"], "type": e["type"]} for e in exc.errors()],
            },
            status_code=422,
        )

    @app.exception_handler(InvalidCommand)
    async def invalid(_request: Request, exc: InvalidCommand) -> JSONResponse:
        status = (
            401
            if isinstance(exc, Unauthorized)
            else 403
            if isinstance(exc, Forbidden)
            else 409
            if isinstance(exc, Conflict)
            else 400
        )
        return JSONResponse({"error": str(exc)}, status_code=status)

    @app.exception_handler(KeyError)
    async def missing(_request: Request, _exc: KeyError) -> JSONResponse:
        return JSONResponse({"error": "Not found"}, status_code=404)

    @app.exception_handler(Unavailable)
    async def unavailable(_request: Request, _exc: Unavailable) -> JSONResponse:
        return JSONResponse({"error": "Service unavailable"}, status_code=503)

    @app.middleware("http")
    async def boundaries(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Bound parsing before framework validation, including streamed/chunked bodies.
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length and (not length.isdecimal() or int(length) > 262144):
                return JSONResponse({"error": "Request too large"}, status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    return JSONResponse({"error": "Request too large"}, status_code=413)
            request._body = bytes(body)
            if (
                request.cookies.get("family_session")
                and not request.headers.get("authorization")
                and request.headers.get("origin") != config.publicUrl.rstrip("/")
            ):
                return JSONResponse({"error": "Same-origin request required"}, status_code=403)
        if request.url.path.startswith(("/api/v1/auth/", "/api/v1/enroll")):
            now = time.monotonic()
            host = request.client.host if request.client else "unknown"
            for key in list(attempts):
                while attempts[key] and attempts[key][0] < now - 60:
                    attempts[key].popleft()
                if not attempts[key]:
                    del attempts[key]
            if len(attempts) >= 10000 or len(attempts[host]) >= 20:
                return JSONResponse({"error": "Too many authentication attempts"}, status_code=429)
            attempts[host].append(now)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def token(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        if authorization:
            if not authorization.startswith("Bearer "):
                raise Unauthorized("Bearer credentials required")
            return authorization[7:]
        return request.cookies.get("family_session", "")

    def principal(request: Request) -> dict:
        peer = request.client.host if request.client else ""
        if peer.startswith("unix-adapter:"):
            kind = peer.removeprefix("unix-adapter:")
            return {"username": "interface:" + kind, "role": "interface", "kind": kind}
        credential = token(request)
        if credential:
            for name, interface in config.interfaces.items():
                try:
                    expected = secret(interface.tokenFile)
                except (OSError, InvalidCommand):
                    LOG.warning("Interface credential unavailable for %s", name)
                    continue
                if hmac.compare_digest(credential, expected):
                    return {
                        "username": "interface:" + name,
                        "role": "interface",
                        "kind": interface.kind,
                    }
        return accounts.authenticate(credential)

    def parent(request: Request) -> dict:
        identity = principal(request)
        if identity["role"] == "interface":
            actor = request.headers.get("x-family-actor", "")
            prefix = identity["kind"] + ":"
            if not actor.startswith(prefix):
                raise Forbidden("Interface must identify its authenticated sender")
            return accounts.interface_actor(identity["kind"], actor[len(prefix) :])
        return identity

    def admin(request: Request) -> dict:
        identity = principal(request)
        if identity["role"] != "admin":
            raise Forbidden("Administrator access required")
        return identity

    def personal(request: Request) -> dict:
        identity = principal(request)
        if identity["role"] == "interface":
            raise Forbidden("Use your personal sign-in to manage your account")
        return identity

    def adapter(request: Request) -> dict:
        identity = principal(request)
        if identity["role"] != "interface":
            raise Forbidden("Integration credentials required")
        return identity

    def device(request: Request, device_id: str) -> dict:
        return backend.authenticate_device(device_id, token(request))

    Parent = Annotated[dict, Depends(parent)]
    Admin = Annotated[dict, Depends(admin)]
    Reader = Annotated[dict, Depends(principal)]
    Device = Annotated[dict, Depends(device)]
    Personal = Annotated[dict, Depends(personal)]
    Adapter = Annotated[dict, Depends(adapter)]
    Kind = Literal["telegram", "matrix"]

    def login_response(credential: str) -> JSONResponse:
        response = JSONResponse(
            {
                "token": credential,
                "tokenType": "Bearer",
                "expiresIn": accounts.config.sessionSeconds,
            }
        )
        response.set_cookie(
            "family_session",
            credential,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=accounts.config.sessionSeconds,
            path="/",
        )
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/auth/register", status_code=201)
    def register(body: Registration) -> dict:
        return accounts.register(body)

    @app.post("/api/v1/auth/login")
    def login(body: Login) -> Response:
        return login_response(accounts.login(body.username, body.password.get_secret_value()))

    @app.post("/api/v1/auth/logout")
    def logout(request: Request) -> Response:
        with backend.store.transaction():
            backend.store.delete("sessions", digest(token(request)))
        response = JSONResponse({"status": "logged-out"})
        response.delete_cookie("family_session", path="/")
        return response

    @app.get("/api/v1/preferences")
    def preferences(identity: Reader) -> dict:
        return integrations.preferences()

    @app.put("/api/v1/preferences")
    def save_preferences(body: LanguagePreferences, identity: Admin) -> dict:
        return integrations.set_preferences(body, identity["username"])

    @app.put("/api/v1/adapter/language")
    def adapter_language(body: LanguageChange, identity: Adapter, actor: Parent) -> dict:
        return integrations.language(identity["kind"], actor["username"], body)

    @app.get("/api/v1/operations/{operation:path}")
    def operation(operation: str, identity: Reader) -> dict:
        record = backend.store.get("commands", operation)
        if not record:
            raise InvalidCommand("Unknown operation")
        account = backend.store.get("accounts", record["actor"]) or {}
        return {**record, "actorName": account.get("displayName") or record["actor"]}

    @app.get("/api/v1/me")
    def me(identity: Parent) -> dict:
        return accounts.public(identity)

    @app.put("/api/v1/me/profile")
    def profile(body: ProfileUpdate, identity: Personal) -> dict:
        return accounts.profile(identity["username"], body)

    @app.post("/api/v1/me/password")
    def password(body: PasswordChange, identity: Personal, request: Request) -> dict:
        accounts.change_password(identity["username"], body, token(request))
        return {"status": "changed"}

    @app.get("/api/v1/me/connections")
    def personal_connections(identity: Personal) -> dict:
        return {"integrations": integrations.overview(identity["username"])}

    @app.post("/api/v1/me/connections/{kind}/challenge")
    def begin_link(kind: Kind, identity: Personal) -> dict:
        return integrations.begin(identity["username"], kind)

    @app.delete("/api/v1/me/connections/{kind}/challenge")
    def cancel_link(kind: Kind, identity: Personal) -> dict:
        with backend.store.transaction():
            backend.store.delete("link-challenges", f"{identity['username']}:{kind}")
        return {"status": "cancelled"}

    @app.post("/api/v1/me/connections/{kind}/confirm")
    def confirm_link(kind: Kind, body: LinkConfirmation, identity: Personal) -> dict:
        integrations.confirm(identity["username"], kind, body)
        return {"status": "linked"}

    @app.delete("/api/v1/me/connections/{kind}")
    def disconnect(kind: Kind, external_id: str, identity: Personal) -> dict:
        integrations.unlink(kind, external_id, identity["username"])
        return {"status": "unlinked"}

    @app.get("/api/v1/integrations")
    def integration_overview(identity: Admin) -> dict:
        return {"integrations": integrations.overview()}

    @app.delete("/api/v1/integrations/{kind}/links")
    def unlink(kind: Kind, external_id: str, identity: Admin) -> dict:
        integrations.unlink(kind, external_id, identity["username"], admin=True)
        return {"status": "unlinked"}

    @app.post("/api/v1/adapter/health")
    def integration_health(body: IntegrationReport, identity: Adapter) -> dict:
        integrations.report(identity["kind"], body)
        return {"status": "recorded"}

    @app.post("/api/v1/adapter/link")
    def integration_claim(body: LinkClaim, identity: Adapter) -> dict:
        integrations.claim(identity["kind"], body)
        return {"status": "claimed"}

    @app.get("/api/v1/authentication")
    def authentication(identity: Personal) -> dict:
        auth = config.authentication
        return {
            "password": auth.password.model_dump(),
            "oidc": auth.oidc.model_dump(),
            "requireApproval": auth.requireApproval,
            "providers": [
                {"id": name, "label": p.label or name, "issuer": p.issuer}
                for name, p in auth.providers.items()
            ],
        }

    @app.get("/api/v1/auth/oidc/{provider}/start")
    def oidc_start(provider: str, invitation: str | None = None) -> Response:
        url, binding = oidc.start(provider, invitation)
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(
            "family_oidc",
            binding,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=300,
            path="/api/v1/auth/oidc/",
        )
        return response

    @app.get("/api/v1/auth/oidc/{provider}/callback")
    def oidc_finish(provider: str, state: str, code: str, request: Request) -> Response:
        account = oidc.finish(provider, state, code, request.cookies.get("family_oidc", ""))
        response = (
            login_response(accounts.session(account, "oidc"))
            if config.authentication.oidc.login and account["status"] == "approved"
            else JSONResponse(accounts.public(account))
        )
        response.delete_cookie("family_oidc", path="/api/v1/auth/oidc/")
        return response

    @app.get("/api/v1/accounts")
    def account_list(identity: Admin) -> dict:
        return {"accounts": [accounts.public(a) for _, a in backend.store.items("accounts")]}

    @app.post("/api/v1/accounts/{username}/approve")
    def account_approve(username: str, body: Approval, identity: Admin) -> dict:
        return accounts.approve(username, body.role, identity["username"])

    @app.put("/api/v1/accounts/{username}/role")
    def account_role(username: str, body: Approval, identity: Admin) -> dict:
        return accounts.set_role(username, body.role, identity["username"])

    @app.post("/api/v1/accounts/{username}/revoke")
    def account_revoke(username: str, identity: Admin) -> dict:
        accounts.revoke(username, identity["username"])
        return {"status": "revoked"}

    @app.post("/api/v1/invitations", status_code=201)
    def invitation(body: Invitation, identity: Admin) -> dict:
        return {"token": accounts.invite(body, identity["username"])}

    @app.post("/api/v1/identities")
    def link(body: IdentityLink, identity: Admin) -> dict:
        accounts.link(body.interface, body.externalId, body.account, identity["username"])
        return {"status": "linked"}

    @app.post("/api/v1/identities/oidc")
    def link_oidc(body: OIDCLink, identity: Admin) -> dict:
        accounts.link_oidc(body.issuer, body.subject, body.account, identity["username"])
        return {"status": "linked"}

    @app.get("/api/v1/children")
    def children(identity: Reader) -> dict:
        return {"children": list(config.children)}

    @app.get("/api/v1/children/{child}/status")
    def status(child: str, identity: Reader) -> dict:
        return backend.status(child)

    @app.get("/api/v1/children/{child}/history")
    def history(child: str, identity: Reader) -> dict:
        return {"events": backend.history(child)}

    @app.get("/api/v1/children/{child}/usage")
    def usage(child: str, identity: Reader, start: date | None = None) -> dict:
        return backend.week(child, start)

    @app.get("/api/v1/children/{child}/policy")
    def policy(child: str, identity: Parent) -> dict:
        return backend.policy(child)

    @app.put("/api/v1/children/{child}/policy")
    def policy_put(
        child: str, body: ChildPolicy, identity: Parent, revision: Annotated[int, Query(ge=1)]
    ) -> dict:
        return backend.set_policy(child, body, revision, identity["username"])

    @app.post("/api/v1/children/{child}/setup")
    def setup_child(
        child: str, body: ChildSetup, identity: Parent, revision: Annotated[int, Query(ge=1)]
    ) -> dict:
        return backend.setup_child(child, body, revision, identity["username"])

    @app.post("/api/v1/children/{child}/commands/{action}", status_code=202)
    def command(
        child: str,
        action: str,
        body: Command,
        identity: Parent,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> dict:
        return backend.command(
            child,
            action,
            body.model_dump(exclude_unset=True),
            idempotency_key,
            identity["username"],
        )

    @app.post("/api/v1/devices/{device_id}/invitation")
    def device_invitation(device_id: str, identity: Admin) -> dict:
        return {"token": backend.enrollment_token(device_id, identity["username"])}

    @app.post("/api/v1/enroll", status_code=202)
    def enroll(body: Enrollment) -> dict:
        return backend.enroll(body)

    @app.get("/api/v1/devices")
    def devices(identity: Admin) -> dict:
        return {"devices": backend.devices()}

    @app.post("/api/v1/devices/{device_id}/approve")
    def device_approve(device_id: str, identity: Admin) -> dict:
        return backend.device_decision(device_id, True, identity["username"])

    @app.post("/api/v1/devices/{device_id}/revoke")
    def device_revoke(device_id: str, identity: Admin) -> dict:
        return backend.device_decision(device_id, False, identity["username"])

    @app.post("/api/v1/devices/{device_id}/sync")
    def sync(device_id: str, body: DeviceReport, identity: Device) -> dict:
        return backend.sync(device_id, body)

    async def event_stream(
        request: Request, audience: str, after: int, check: Callable[[], object]
    ) -> AsyncIterator[str]:
        cursor = after
        deadline = time.monotonic() + 20
        # Bound connection lifetime so a graceful backend stop cannot wait indefinitely.
        # Consumers resume from their persisted cursor when the stream closes.
        while time.monotonic() < deadline and not await request.is_disconnected():
            try:
                check()
            except (Unauthorized, Forbidden):
                return
            backend.reconcile_notifications()
            batch = backend.store.events(audience, cursor)
            for event in batch:
                yield f"id: {event['id']}\nevent: change\ndata: {json.dumps(event)}\n\n"
                cursor = event["id"]
            if not batch:
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

    @app.get("/api/v1/events")
    def events(
        request: Request,
        identity: Reader,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[int | None, Header(ge=0)] = None,
    ) -> Response:
        return StreamingResponse(
            event_stream(
                request,
                "parents",
                last_event_id if last_event_id is not None else after,
                lambda: principal(request),
            ),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/devices/{device_id}/events")
    def device_events(
        device_id: str, request: Request, identity: Device, after: Annotated[int, Query(ge=0)] = 0
    ) -> Response:
        return StreamingResponse(
            event_stream(request, "device:" + device_id, after, lambda: device(request, device_id)),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    return app
