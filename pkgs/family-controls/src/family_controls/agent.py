"""Autonomous device synchronization; no inbound network management listener."""

import logging
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from .adapters.client import BackendClient
from .adapters.http import RemoteError
from .adapters.sqlite import Repository
from .adapters.timekpr import validate_config
from .application import Service
from .config import AgentConfig, User, secret
from .domain import Conflict, InvalidCommand, Unavailable

LOG = logging.getLogger(__name__)


class AgentSync:
    def __init__(self, service: Service, repository: Repository, client: BackendClient):
        self.service, self.repository, self.client = service, repository, client
        self.device = service.config.agent.deviceId
        self.route = "/api/v1/devices/" + self.device
        self.policy_ready = False

    def exchange(self) -> None:
        cached = self.repository.relay_get("backend-policy", {"revision": 0})
        outcomes = self.repository.relay_get("backend-outcomes", {})
        if not isinstance(cached, dict) or not isinstance(outcomes, dict):
            raise InvalidCommand("Corrupt synchronization state")
        if not self.policy_ready:
            initial_results = [
                self.service.reconcile(user, force=True)
                for user in self.service.config.managedUsers
            ]
            self.policy_ready = all(initial_results)
        statuses = {user: self.service.status(user) for user in self.service.config.managedUsers}
        applied = self.policy_ready and all(
            s["application"]["status"] == "applied" for s in statuses.values()
        )
        for operation, outcome in outcomes.items():
            receipt = self.repository.operation(operation)
            if (
                receipt
                and outcome["status"] == "accepted"
                and receipt[1] in {"applied", "superseded"}
            ):
                outcome["status"] = receipt[1]
        result = self.client.request(
            self.route + "/sync",
            {
                "policyRevision": cached["revision"] if applied else 0,
                "statuses": statuses,
                "outcomes": list(outcomes.values())[:100],
            },
        )
        # Outcomes remain until acknowledged by a successful exchange.
        acknowledged = set(list(outcomes)[:100])
        outcomes = {
            key: value
            for key, value in outcomes.items()
            if key not in acknowledged or value["status"] == "accepted"
        }
        policies = result["policies"]
        users = {policy["user"]: policy["policy"] for policy in policies.values()}
        if set(users) != set(self.service.config.managedUsers):
            raise InvalidCommand("Backend assignment must match configured local users")
        if any(p["timezone"] != self.service.config.timezone for p in policies.values()):
            raise InvalidCommand("Backend and device policy timezones differ")
        revision = sum(policy["revision"] for policy in policies.values())
        if cached.get("users") != users or cached["revision"] != revision:
            candidate = self.service.config.model_copy(deep=True)
            candidate.managedUsers = {
                user: User.model_validate(value) for user, value in users.items()
            }
            validate_config(candidate)
            self.repository.relay_set("backend-policy", {"revision": revision, "users": users})
            self.policy_ready = False
            applied_users = []
            for user, cfg in candidate.managedUsers.items():
                with self.service.command_lock(user):
                    self.service.config.managedUsers[user] = cfg
                    self.service.last_apply.pop(user, None)
                    applied_users.append(self.service.reconcile(user, force=True))
            self.policy_ready = all(applied_users)
        for command in result["commands"]:
            operation = command["operationId"]
            receipt = self.repository.operation(operation)
            try:
                if command["user"] not in users:
                    raise InvalidCommand("Command targets an unassigned local user")
                if not receipt and command.get("cancelled"):
                    raise InvalidCommand("Submitting account was revoked before delivery")
                if (
                    not receipt
                    and datetime.fromisoformat(command["expiresAt"]) <= self.service.clock.now()
                ):
                    outcome = {"operationId": operation, "status": "expired"}
                else:
                    self.service.execute(
                        command["user"],
                        command["action"],
                        command["payload"],
                        operation,
                        command["actor"],
                    )
                    receipt = self.repository.operation(operation)
                    outcome = {
                        "operationId": operation,
                        "status": receipt[1] if receipt and receipt[1] != "pending" else "accepted",
                    }
            except (Conflict, InvalidCommand, KeyError) as exc:
                outcome = {"operationId": operation, "status": "failed", "error": str(exc)[:300]}
            except Unavailable:
                # Preserve ordering for commands that depend on fresh observations.
                break
            outcomes[operation] = outcome
            self.repository.relay_set("backend-outcomes", outcomes)
        self.repository.relay_set("backend-outcomes", outcomes)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.exchange()
                progress = self.repository.relay_get("backend-cursor", {"id": 0})
                cursor = progress["id"] if isinstance(progress, dict) else 0
                last_sync = time.monotonic()
                for event in self.client.events(self.route + "/events", cursor):
                    if stop.is_set():
                        return
                    if event is not None or time.monotonic() - last_sync >= 3:
                        self.exchange()
                        last_sync = time.monotonic()
                        if event:
                            self.repository.relay_set("backend-cursor", {"id": event["id"]})
            except (Unavailable, InvalidCommand, OSError):
                LOG.warning("Backend synchronization unavailable; local enforcement continues")
                stop.wait(3)


def device_client(config: AgentConfig) -> BackendClient:
    if config.agent.backend is None:
        raise InvalidCommand("An outbound backend connection is required")
    credential_path = Path(config.agent.stateDir) / "device-credential"
    if not credential_path.exists():
        credential_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with credential_path.open("x") as output:
            credential_path.chmod(0o600)
            output.write(secrets.token_urlsafe(48))
    return BackendClient(config.agent.backend, secret(str(credential_path)))


def enroll_until_approved(
    config: AgentConfig, client: BackendClient, stop: threading.Event
) -> bool:
    while not stop.is_set():
        try:
            # Registration retries are safe after a lost response; credentials are durable.
            if config.agent.enrollmentTokenFile and Path(config.agent.enrollmentTokenFile).exists():
                result = client.request(
                    "/api/v1/enroll",
                    {
                        "device": config.agent.deviceId,
                        "credential": client.credential,
                        "token": secret(config.agent.enrollmentTokenFile),
                    },
                )
                if result["status"] == "approved":
                    return True
            else:
                # An already enrolled device does not need to retain its one-use token.
                iterator = client.events("/api/v1/devices/" + config.agent.deviceId + "/events", 0)
                next(iterator)
                iterator.close()
                return True
        except (Unavailable, RemoteError, OSError):
            LOG.warning("Device enrollment pending or unavailable")
        stop.wait(5)
    return False
