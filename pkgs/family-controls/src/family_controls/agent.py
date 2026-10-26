"""Autonomous device synchronization; no inbound network management listener."""

import logging
import secrets
import threading
import time
from pathlib import Path

from .adapters.client import BackendClient
from .adapters.http import RemoteError
from .adapters.sqlite import Repository
from .adapters.timekpr import validate_config
from .application import Service
from .config import AgentConfig, User, secret
from .domain import InvalidCommand, Unavailable

LOG = logging.getLogger(__name__)


class AgentSync:
    def __init__(self, service: Service, repository: Repository, client: BackendClient):
        self.service, self.repository, self.client = service, repository, client
        self.device = service.config.agent.deviceId
        self.route = "/api/v1/devices/" + self.device
        self.policy_ready = False

    def exchange(self) -> None:
        from .local_usage import LocalUsage

        if self.service.usage is None:
            self.service.usage = LocalUsage(self.service)
        usage = self.service.usage
        for user in self.service.config.managedUsers:
            self.service.reconcile(user)
        statuses = {user: self.service.status(user) for user in self.service.config.managedUsers}
        reports = usage.reports()
        cached = self.repository.relay_get("backend-policy", {"revision": 0})
        if not isinstance(cached, dict):
            raise InvalidCommand("Corrupt cached backend policy")
        result = self.client.request(
            self.route + "/sync",
            {
                "policyRevision": cached["revision"],
                "statuses": statuses,
                "usage": reports,
            },
        )
        policies = result["policies"]
        users = {policy["user"]: policy["policy"] for policy in policies.values()}
        if set(users) != set(self.service.config.managedUsers):
            raise InvalidCommand("Backend assignment must match configured local users")
        if any(p["timezone"] != self.service.config.timezone for p in policies.values()):
            raise InvalidCommand("Backend and device policy timezones differ")
        candidate = self.service.config.model_copy(deep=True)
        candidate.managedUsers = {user: User.model_validate(value) for user, value in users.items()}
        validate_config(candidate)
        revision = sum(policy["revision"] for policy in policies.values())
        self.repository.relay_set("backend-policy", {"revision": revision, "users": users})
        for report in reports:
            self.repository.relay_set(
                f"usage-ack/{report['user']}/{report['day']}", report["sequence"]
            )
        for child, policy in policies.items():
            usage.accept(
                policy["user"],
                {**policy, "child": child},
                statuses[policy["user"]].get("request"),
            )

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
            except (Unavailable, InvalidCommand, OSError) as exc:
                LOG.warning(
                    "Backend synchronization unavailable; local enforcement continues: %s", exc
                )
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
