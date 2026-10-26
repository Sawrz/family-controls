import json
import sqlite3
from contextlib import closing

import pytest
from family_controls.adapters.sqlite import Repository
from family_controls.adapters.timekpr import Timekpr, render_main
from family_controls.domain import Policy, Unavailable, Window


def test_legacy_migration_preserves_records_and_backs_up(tmp_path, clock):
    path = tmp_path / "state.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE state (user TEXT, key TEXT, value TEXT, PRIMARY KEY(user,key))")
    values = [
        (
            "child",
            "effective_overlay",
            json.dumps({"date": "2026-10-18", "capMinutes": 15, "until": "19:00"}),
        ),
        ("child", "pending_request", '{"requestedAt":"2026-10-18T11:59:59"}'),
        ("child", "requests_muted", "1"),
    ]
    db.executemany("INSERT INTO state VALUES(?,?,?)", values)
    db.commit()
    db.close()
    repo = Repository(str(path))
    try:
        state = repo.load("child", clock.now())
        assert state.delta_seconds == 900 and state.until == 19 * 3600 and state.muted
        assert state.request.active(clock.now())
        assert repo.db.execute("SELECT * FROM state ORDER BY key").fetchall() == sorted(
            values, key=lambda row: row[1]
        )
        assert path.with_name("state.sqlite.before-v2").exists()
        request_id = state.request.id
        with repo.transaction():
            repo.save("child", state)
        assert repo.load("child", clock.now()).request.id == request_id
    finally:
        repo.close()


def test_transaction_rolls_back_on_failure(setup):
    with pytest.raises(RuntimeError), setup.repository.transaction():
        setup.repository.audit("child", "never-committed", {}, setup.clock.now())
        raise RuntimeError("interrupted")
    assert setup.repository.history("child") == []


def test_newer_schema_is_not_downgraded(tmp_path):
    path = tmp_path / "future.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        Repository(str(path))


class Bus:
    def __init__(self):
        self.values = {
            "TIME_LEFT_DAY": 4000,
            "PLAYTIME_LEFT_DAY": 100,
            "ACTUAL_PLAYTIME_LEFT_DAY": 59,
            "PLAYTIME_SPENT_DAY": 1,
            "ACTUAL_TIME_LEFT_CONTINUOUS": 60,
        }
        self.users = {}
        self.initial = dict(self.values)
        self.calls = []
        self.fail = False
        self.drift = False

    def select(self, user):
        if user not in self.users:
            self.users[user] = self.values if not self.users else dict(self.initial)
        self.values = self.users[user]

    def setAllowedHours(self, user, day, hours, timeout):
        self.select(user)
        self.calls.append(("hours", user, day, hours, timeout))
        self.values["ALLOWED_HOURS_" + day] = hours
        return (-1 if self.fail else 0, "result")

    def setPlayTimeLimitsForDays(self, user, limits, timeout):
        self.select(user)
        self.calls.append(("limits", user, limits))
        self.values["PLAYTIME_LIMITS_PER_WEEKDAYS"] = limits if not self.drift else []
        return (0, "ok")

    def getUserInformation(self, user, level, timeout):
        self.select(user)
        return (0, "ok", self.values)


def test_dbus_empty_schedule_and_readback(setup):
    bus = Bus()
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    adapter.apply("child", Policy((), (), 0, True), setup.clock.now())
    today = str(setup.clock.now().isoweekday())
    assert bus.values["ALLOWED_HOURS_" + today] == {}
    assert bus.values["PLAYTIME_LIMITS_PER_WEEKDAYS"][int(today) - 1] == 0
    assert len(bus.calls) == 8
    assert adapter.observe("child").play_left == 59
    assert adapter.observe("child").allowed_left == 60


def test_semantic_dbus_failure_and_drift_are_not_success(setup):
    bus = Bus()
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    policy = Policy((Window(0, 86400),), (), None, False)
    bus.fail = True
    with pytest.raises(Unavailable, match="rejected"):
        adapter.apply("child", policy, setup.clock.now())
    bus.fail, bus.drift = False, True
    with pytest.raises(Unavailable, match="readback"):
        adapter.apply("child", policy, setup.clock.now())


def test_invalid_telemetry_is_unknown(setup):
    bus = Bus()
    bus.values.clear()
    with pytest.raises(Unavailable):
        Timekpr(setup.config, setup.clock, lambda: bus).observe("child")


def test_transport_error_is_bounded_and_sanitized(setup, monkeypatch):
    bus = Bus()

    def fail(*args, **kwargs):
        assert kwargs["timeout"] == 5
        raise OSError("private internals")

    monkeypatch.setattr(bus, "getUserInformation", fail)
    with pytest.raises(Unavailable, match="unavailable"):
        Timekpr(setup.config, setup.clock, lambda: bus).observe("child")


def test_restore_restores_all_recurring_days(setup):
    bus = Bus()
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    adapter.restore()
    assert bus.values["PLAYTIME_LIMITS_PER_WEEKDAYS"] == [3600] * 7
    assert len(bus.calls) == 16


def test_renderer_leaves_usage_files_and_identifiers_intact(setup, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(setup.config.model_dump_json())
    target = tmp_path / "timekpr"
    target.mkdir()
    usage = target / "child.time"
    usage.write_bytes(b"existing usage sentinel")
    monkeypatch.setattr("sys.argv", ["render", "--config", str(config), "--directory", str(target)])
    render_main()
    assert usage.read_bytes() == b"existing usage sentinel"
    assert "[child]" in (target / "timekpr.child.conf").read_text()
    assert "PLAYTIME_LIMITS_PER_WEEKDAYS = 3600;3600" in (target / "timekpr.child.conf").read_text()


def test_periodic_readback_repairs_drift_without_rewriting_unchanged_policy(setup):
    bus = Bus()
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    policy = Policy((Window(0, 86400),), (), 3600, False)
    adapter.apply("child", policy, setup.clock.now())
    bus.calls.clear()
    adapter.apply("child", policy, setup.clock.now())
    assert bus.calls == []
    bus.values["PLAYTIME_LIMITS_PER_WEEKDAYS"] = [999] * 7
    adapter.apply("child", policy, setup.clock.now())
    assert bus.calls == [("limits", "child", [3600] * 7)]


def test_expired_relay_prompts_and_actor_claims_are_pruned_but_receipts_remain(setup):
    repo = setup.repository
    repo.relay_set("button:old", {"expires": "2020-01-01T00:00:00+00:00"})
    repo.claim("button-actor:old", "parent")
    repo.relay_set("transport", {"offset": 123, "pending": []})
    setup.service.execute("child", "add-time", {"minutes": 25}, "retained", "parent")
    repo.prune_relay(setup.clock.now())
    repo.prune(setup.clock.now(), 1)
    assert repo.relay_get("button:old") is None
    assert repo.relay_get("button-actor:old") is None
    assert repo.relay_get("transport")["offset"] == 123
    assert repo.operation("retained")[1] == "applied"
    assert repo.history("child")[0]["outcome"] == "applied"


def test_limit_readback_with_stale_remaining_never_fabricates_usage(setup):
    bus = Bus()
    bus.values.update(
        {
            "PLAYTIME_LIMITS_PER_WEEKDAYS": [86400] * 7,
            "PLAYTIME_SPENT_DAY": 17,
            "ACTUAL_PLAYTIME_LEFT_DAY": 23,
            "ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT": 1,
        }
    )
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    assert adapter.observe("child").raw["playSeconds"] == 17
    bus.values["ACTUAL_PLAYTIME_LEFT_DAY"] = 86383
    assert adapter.observe("child").raw["playSeconds"] == 17
