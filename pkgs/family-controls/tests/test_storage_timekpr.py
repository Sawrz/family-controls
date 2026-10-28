import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest
from family_controls.adapters.backend_sqlite import BackendStore
from family_controls.adapters.sqlite import Repository
from family_controls.adapters.timekpr import Timekpr, render_main
from family_controls.backend.usage import UsageLedger
from family_controls.domain import Policy, Unavailable, Window
from family_controls.local_usage import LocalUsage


@pytest.fixture
def saved_usage(setup, tmp_path):
    setup.config.timekpr.workDir = str(tmp_path / "timekpr-work")
    Path(setup.config.timekpr.workDir).mkdir()

    def write(seconds, checked_at=None):
        checked_at = checked_at or setup.clock.now().replace(tzinfo=None).isoformat(sep=" ")
        path = Path(setup.config.timekpr.workDir) / "child.time"
        path.write_text(
            f"[child]\nLAST_CHECKED = {checked_at}\n"
            f"[child.PLAYTIME]\nPLAYTIME_SPENT_DAY = {seconds}\n"
        )
        return path

    write(1)
    return write


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
            "TRACK_INACTIVE": False,
            "LOCKOUT_TYPE": "kill",
            "PLAYTIME_ACTIVITIES": [["test-game", ""]],
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

    def setTrackInactive(self, user, value, timeout):
        self.select(user)
        self.values["TRACK_INACTIVE"] = value
        self.calls.append(("inactive", user, value))
        return (0, "ok")

    def setLockoutType(self, user, value, wake_from, wake_to, timeout):
        self.select(user)
        self.values["LOCKOUT_TYPE"] = value
        self.calls.append(("lockout", user, value))
        return (0, "ok")

    def setPlayTimeActivities(self, user, value, timeout):
        self.select(user)
        if not self.drift:
            self.values["PLAYTIME_ACTIVITIES"] = value
        self.calls.append(("activities", user, value))
        return (0, "ok")


def test_live_activity_and_session_changes_are_verified_without_resetting_usage(setup):
    bus = Bus()
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    policy = Policy((Window(0, 86400),), (), 3600, False)
    adapter.apply("child", policy, setup.clock.now())
    cfg = setup.config.managedUsers["child"]
    cfg.playTime.activities = [".*new-game.*[New game]", '.*other.*("Other")']
    cfg.trackInactive = True
    cfg.killSessions = False
    bus.calls.clear()
    adapter.apply("child", policy, setup.clock.now())
    assert bus.calls == [
        ("inactive", "child", True),
        ("lockout", "child", "terminate"),
        ("activities", "child", [[".*new-game.*", "New game"], [".*other.*", "Other"]]),
    ]
    assert bus.values["PLAYTIME_SPENT_DAY"] == 1
    bus.calls.clear()
    adapter.apply("child", policy, setup.clock.now())
    assert not bus.calls
    bus.drift = True
    cfg.playTime.activities = [".*"]
    with pytest.raises(Unavailable, match="settings differ"):
        adapter.apply("child", policy, setup.clock.now())


def test_dbus_empty_schedule_and_readback(setup, saved_usage):
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


def test_limit_readback_with_stale_remaining_never_fabricates_usage(setup, saved_usage):
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
    saved_usage(17)
    assert adapter.observe("child").raw["playSeconds"] == 17
    bus.values["ACTUAL_PLAYTIME_LEFT_DAY"] = 86383
    assert adapter.observe("child").raw["playSeconds"] == 17


@pytest.mark.parametrize(
    "last_checked,restart",
    [
        ("2026-10-27 23:59:59", False),
        ("2026-10-27 23:59:59", True),
        ("2026-12-31 23:59:59", False),
        ("2026-10-25 23:59:59", False),
    ],
    ids=["delayed-reset", "agent-restart", "year-rollover", "after-dst"],
)
def test_delayed_native_reset_does_not_charge_yesterday_to_today(
    setup, saved_usage, last_checked, restart
):
    clock = setup.clock
    clock.value = datetime.fromisoformat(last_checked).replace(tzinfo=clock.now().tzinfo)
    bus = Bus()
    bus.values["PLAYTIME_SPENT_DAY"] = 10400
    saved_usage(10400, last_checked)
    adapter = Timekpr(setup.config, clock, lambda: bus)
    usage = LocalUsage(setup.service)
    policy = Policy((), (), 5400, False)
    usage.observe("child", adapter.observe("child"), policy)
    yesterday = usage.read("child")
    assert yesterday.playSeconds == 10400

    clock.advance(seconds=2)
    if restart:
        usage = LocalUsage(setup.service)
    usage.observe("child", adapter.observe("child"), policy)
    assert usage.read("child").playSeconds == 0
    assert usage.cap("child", clock.now()) == 0

    # The file and D-Bus response may come from different sides of the reset.
    saved_usage(0)
    usage.observe("child", adapter.observe("child"), policy)
    saved_usage(4)
    usage.observe("child", adapter.observe("child"), policy)
    usage.observe("child", adapter.observe("child"), policy)
    assert usage.read("child").playSeconds == 4
    assert usage.read("child", yesterday.day) == yesterday


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "[child]\nLAST_CHECKED = invalid\n[child.PLAYTIME]\nPLAYTIME_SPENT_DAY = 10\n",
        "[child]\nLAST_CHECKED = 2026-10-18 12:00:00\n[child.PLAYTIME]\nPLAYTIME_SPENT_DAY = -1\n",
        "[child]\nLAST_CHECKED = 2026-10-19 12:00:00\n[child.PLAYTIME]\nPLAYTIME_SPENT_DAY = 10\n",
        "[child]\nLAST_CHECKED = 2026-10-18 12:00:00\n[child.PLAYTIME]\n",
    ],
    ids=[
        "missing",
        "empty-write",
        "invalid-date",
        "negative-counter",
        "future-day",
        "partial-write",
    ],
)
def test_undated_or_invalid_usage_never_falls_back_to_dbus(setup, saved_usage, content):
    path = saved_usage(1)
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    bus = Bus()
    with pytest.raises(Unavailable):
        Timekpr(setup.config, setup.clock, lambda: bus).observe("child")


def test_observation_crossing_midnight_is_retried_before_accounting(setup):
    usage = LocalUsage(setup.service)
    setup.clock.value = setup.clock.now().replace(hour=23, minute=59, second=59)
    observation = setup.enforcer.observe("child")
    setup.clock.advance(seconds=2)
    with pytest.raises(Unavailable, match="policy day"):
        usage.observe("child", observation, Policy((), (), 5400, False))
    assert setup.repository.relay_items("usage/") == []


def test_fresh_allowance_is_allocated_while_native_reset_is_pending(setup, saved_usage, tmp_path):
    saved_usage(10400, "2026-10-17 23:59:59")
    bus = Bus()
    bus.values["PLAYTIME_SPENT_DAY"] = 10400
    adapter = Timekpr(setup.config, setup.clock, lambda: bus)
    usage = LocalUsage(setup.service)
    policy = Policy((), (), 5400, False)
    usage.observe("child", adapter.observe("child"), policy)
    report = usage.read("child")
    report.policyRevision = 1
    report.needsTime = True
    store = BackendStore(str(tmp_path / "backend.sqlite"))
    try:
        ledger = UsageLedger(store)
        with store.transaction():
            ledger.accept("child", "device", report, report.day)
            allocation = ledger.allocate("child", "device", report, report.day, 5400, 1)
        assert allocation.reportedUsedSeconds == 0
        assert allocation.issuedSeconds == 300
        assert allocation.availableSeconds + allocation.reservedSeconds == 5400
        report.issuedSeconds = allocation.issuedSeconds
        usage.save(report)
        assert usage.cap("child", setup.clock.now()) == 300

        saved_usage(4)
        usage.observe("child", adapter.observe("child"), policy)
        report = usage.read("child")
        with store.transaction():
            ledger.accept("child", "device", report, report.day)
        balance = ledger.balance("child", report.day, 5400)
        assert balance["reportedUsedSeconds"] == 4
        assert balance["availableSeconds"] + balance["reservedSeconds"] == 5396
        assert report.issuedSeconds - report.playSeconds == 296
    finally:
        store.close()


def test_work_file_changed_during_read_is_retried(setup, saved_usage, monkeypatch):
    from family_controls.adapters import timekpr

    saved_usage(1)
    fstat = timekpr.os.fstat
    calls = 0

    def changed(fileno):
        nonlocal calls
        stat = fstat(fileno)
        calls += 1
        if calls == 1:
            saved_usage(100)
        return stat

    monkeypatch.setattr(timekpr.os, "fstat", changed)
    bus = Bus()
    with pytest.raises(Unavailable, match="changed while reading"):
        Timekpr(setup.config, setup.clock, lambda: bus).observe("child")
