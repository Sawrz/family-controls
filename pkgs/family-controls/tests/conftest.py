import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from family_controls.adapters.sqlite import Repository
from family_controls.adapters.timekpr import hour_map
from family_controls.application import Service
from family_controls.config import AgentConfig
from family_controls.domain import Observation, Unavailable


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0
        self.value = dt.datetime(2026, 10, 18, 12, tzinfo=ZoneInfo("Europe/Berlin"))

    def now(self):
        return self.value

    def monotonic(self):
        return self.elapsed

    def advance(self, **kwargs):
        interval = dt.timedelta(**kwargs)
        self.elapsed += max(0, interval.total_seconds())
        self.value += interval


class FakeEnforcer:
    def __init__(self, clock):
        self.clock = clock
        self.used = {}
        self.policies = {}
        self.calls = []
        self.offline = False

    def validate(self, policy):
        hour_map(policy.windows, policy.homework)

    def apply(self, user, policy, now):
        if self.offline:
            raise Unavailable("Test outage")
        self.calls.append((user, policy))
        self.policies[user] = policy

    def observe(self, user):
        if self.offline:
            raise Unavailable("Test outage")
        policy = self.policies[user]
        limit = 86400 if policy.limit_seconds is None else policy.limit_seconds
        return Observation(
            max(0, limit - self.used.get(user, 0)),
            86400,
            self.clock.now(),
            {"playSeconds": self.used.get(user, 0), "activeActivities": 1},
        )

    def restore(self):
        self.policies.clear()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def setup(tmp_path, clock):
    config = AgentConfig.model_validate(
        {
            "timezone": "Europe/Berlin",
            "agent": {
                "stateDir": str(tmp_path),
                "socketPath": str(tmp_path / "control.sock"),
                "socketGroup": None,
            },
            "managedUsers": {
                name: {
                    "playTime": {"dailyLimitMinutes": 60, "activities": ["test-game"]},
                    "timeWindows": {"allowed": {"ALL": ["09:00-18:00"]}},
                }
                for name in ("child", "sibling")
            },
        }
    )
    repository = Repository(str(tmp_path / "state.sqlite"))
    enforcer = FakeEnforcer(clock)
    service = Service(config, repository, enforcer, clock)
    for user in config.managedUsers:
        service.reconcile(user)
    yield SimpleNamespace(
        config=config, repository=repository, enforcer=enforcer, service=service, clock=clock
    )
    repository.close()
