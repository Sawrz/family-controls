from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from family_controls.application import Service
from family_controls.domain import Conflict, InvalidCommand, Unavailable


def command(setup, action, payload=None, operation=None, actor="parent"):
    return setup.service.execute("child", action, payload or {}, operation or str(uuid4()), actor)


def test_add_and_restore_preserves_consumed_time(setup):
    setup.enforcer.used["child"] = 2400
    result = command(setup, "add-time", {"minutes": 25})
    assert result["remainingPlayTimeSeconds"] == 2700
    result = command(setup, "restore-today")
    assert result["remainingPlayTimeSeconds"] == 1200


def test_concurrent_grants_are_atomic(setup):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda minutes: command(setup, "add-time", {"minutes": minutes}), (5, 15)))
    assert setup.service.status("child")["effectiveDailyLimitMinutes"] == 80


def test_duplicate_operation_survives_application_restart(setup):
    command(setup, "add-time", {"minutes": 5}, "same")
    setup.service = Service(setup.config, setup.repository, setup.enforcer, setup.clock)
    result = command(setup, "add-time", {"minutes": 5}, "same")
    assert result["duplicate"] and result["effectiveDailyLimitMinutes"] == 65
    assert len(setup.repository.history("child")) == 1


def test_reusing_id_with_other_payload_is_conflict(setup):
    command(setup, "add-time", {"minutes": 5}, "same")
    with pytest.raises(Conflict):
        command(setup, "add-time", {"minutes": 15}, "same")


@pytest.mark.parametrize(
    "payload",
    [
        {"minutes": True},
        {"minutes": -1},
        {"minutes": 1441},
        {"minutes": "5"},
        {"minutes": 5, "extra": 1},
    ],
)
def test_invalid_commands_do_not_change_state(setup, payload):
    before = setup.repository.db.total_changes
    with pytest.raises(InvalidCommand):
        command(setup, "add-time", payload)
    assert setup.repository.db.total_changes == before


def test_invalid_until_does_not_poison_state(setup):
    with pytest.raises(InvalidCommand):
        command(setup, "extend-access", {"minutes": 15, "until": "invalid"})
    assert setup.repository.load("child", setup.clock.now()).until is None
    assert setup.service.reconcile("child")


def test_status_is_read_only(setup):
    before = setup.repository.db.total_changes
    setup.service.status("child")
    setup.service.status("child")
    assert setup.repository.db.total_changes == before


def test_partial_enforcement_failure_is_durable_and_truthful(setup):
    setup.enforcer.offline = True
    result = command(setup, "add-time", {"minutes": 25}, "retry")
    assert result["application"]["status"] == "pending"
    setup.enforcer.offline = False
    result = command(setup, "add-time", {"minutes": 25}, "retry")
    assert result["effectiveDailyLimitMinutes"] == 85
    assert result["application"]["status"] == "applied"


def test_unknown_telemetry_is_not_zero_time(setup):
    setup.enforcer.offline = True
    setup.clock.advance(minutes=1)
    setup.service.reconcile("child")
    status = setup.service.status("child")
    assert status["phase"] == "unknown" and status["remainingCountdownSeconds"] is None


def test_set_total_and_remaining_are_distinct(setup):
    setup.enforcer.used["child"] = 2400
    assert command(setup, "set-limit", {"minutes": 90})["remainingPlayTimeSeconds"] == 3000
    assert command(setup, "set-remaining", {"minutes": 20})["remainingPlayTimeSeconds"] == 1200


def test_deduct_final_partial_minute(setup):
    setup.enforcer.used["child"] = 3541
    setup.service.reconcile("child")
    assert command(setup, "deduct-time", {"minutes": 1})["remainingPlayTimeSeconds"] == 0


def test_set_remaining_requires_recent_usage(setup):
    setup.clock.advance(minutes=1)
    with pytest.raises(Unavailable):
        command(setup, "set-remaining", {"minutes": 10})


def test_set_remaining_includes_overrun_and_locked_usage(setup):
    setup.enforcer.used["child"] = 4000
    state = setup.repository.load("child", setup.clock.now())
    state.locked_seconds = 120
    with setup.repository.transaction():
        setup.repository.save("child", state)
    setup.service.reconcile("child")
    assert setup.service.status("child")["remainingPlayTimeSeconds"] == 0
    result = command(setup, "set-remaining", {"minutes": 20})
    assert result["remainingPlayTimeSeconds"] == 1200
    assert result["effectiveDailyLimitMinutes"] * 60 == 5320


@pytest.mark.parametrize("used", [None, True, -1])
def test_set_remaining_rejects_missing_or_invalid_consumed_counter(setup, used):
    setup.service.observations["child"] = replace(
        setup.service.observations["child"], raw={"playSeconds": used}
    )
    with pytest.raises(Unavailable, match="Measured usage"):
        command(setup, "set-remaining", {"minutes": 20})


def test_unlimited_expiry_restores_budget(setup):
    assert command(setup, "unlimited-today")["effectiveDailyLimitMinutes"] is None
    setup.clock.advance(days=1)
    setup.service.reconcile("child")
    assert setup.service.status("child")["effectiveDailyLimitMinutes"] == 60


def test_manual_block_wins_until_explicit_unblock(setup):
    command(setup, "block", {"days": 3})
    assert command(setup, "unlimited-today")["phase"] == "blocked"
    with pytest.raises(Conflict):
        command(setup, "allow-today")
    assert command(setup, "unblock")["phase"] == "idle"


def test_session_grant_and_until_have_different_effects(setup):
    setup.clock.value = setup.clock.now().replace(hour=19)
    setup.enforcer.used["child"] = 3600
    result = command(setup, "grant-session", {"minutes": 25})
    assert result["remainingCountdownSeconds"] == 1500
    assert result["allowedUntil"] == "19:25"
    result = command(setup, "allow-until", {"until": "20:00"})
    assert result["effectiveDailyLimitMinutes"] == 85


def test_request_remains_visible_when_time_expires(setup):
    command(setup, "request-time", {"minutes": 25, "reason": "Finish homework"})
    setup.enforcer.used["child"] = 3600
    setup.service.reconcile("child")
    status = setup.service.status("child")
    assert status["phase"] == "expired" and status["pendingRequest"]


def test_yesterdays_request_expires_and_new_request_can_be_created(setup):
    first = command(setup, "request-time")["pendingRequest"]["id"]
    setup.clock.advance(days=1)
    second = command(setup, "request-time")["pendingRequest"]["id"]
    assert second != first


def test_two_parents_can_only_decide_request_once(setup):
    request = command(setup, "request-time")["pendingRequest"]["id"]
    body = {"requestId": request, "decision": "grant-session", "minutes": 15}
    command(setup, "decide-request", body, actor="parent-a")
    with pytest.raises(Conflict):
        command(setup, "decide-request", body, actor="parent-b")
    assert setup.service.status("child")["effectiveDailyLimitMinutes"] == 75


def test_denial_cancel_and_cooldown(setup):
    request = command(setup, "request-time")["pendingRequest"]["id"]
    result = command(
        setup, "decide-request", {"requestId": request, "decision": "deny", "reason": "Bedtime"}
    )
    assert result["request"]["status"] == "denied"
    with pytest.raises(Conflict):
        command(setup, "request-time")
    setup.clock.advance(seconds=61)
    request = command(setup, "request-time")["pendingRequest"]["id"]
    assert (
        command(setup, "cancel-request", {"requestId": request})["request"]["status"] == "cancelled"
    )


def test_muting_does_not_hide_existing_request(setup):
    command(setup, "request-time")
    assert command(setup, "mute")["pendingRequest"] is not None
    command(setup, "unmute")
    assert not setup.service.status("child")["muted"]


def test_revision_conflict_and_unknown_user(setup):
    with pytest.raises(Conflict):
        command(setup, "add-time", {"minutes": 5, "expectedRevision": 99})
    with pytest.raises(KeyError):
        setup.service.status("stranger")


def test_unchanged_policy_is_not_rewritten_each_poll(setup):
    before = len(setup.enforcer.calls)
    for _ in range(5):
        setup.service.reconcile("child")
    assert len(setup.enforcer.calls) == before
    setup.clock.advance(seconds=31)
    setup.service.reconcile("child")
    assert len(setup.enforcer.calls) == before + 1


def test_locked_accounting_measures_elapsed_and_preserves_usage_on_restore(setup):
    from types import SimpleNamespace

    setup.config.managedUsers["child"].countLockedTime = True
    setup.service.sessions = SimpleNamespace(all_locked=lambda user: True)
    setup.service.reconcile("child")
    setup.clock.advance(seconds=3)
    setup.service.reconcile("child")
    status = setup.service.status("child")
    assert status["countedLockedSeconds"] == 3
    assert status["remainingPlayTimeSeconds"] == 3597
    assert status["effectiveDailyLimitMinutes"] == 60
    assert command(setup, "restore-today")["remainingPlayTimeSeconds"] == 3597
    setup.clock.advance(days=1)
    setup.service.reconcile("child")
    assert setup.service.status("child")["countedLockedSeconds"] == 0


def test_locked_accounting_excludes_native_usage_unknown_gaps_and_unlocked_sessions(setup):
    from types import SimpleNamespace

    sessions = SimpleNamespace(locked=True)
    sessions.all_locked = lambda user: sessions.locked
    setup.config.managedUsers["child"].countLockedTime = True
    setup.service.sessions = sessions
    setup.service.reconcile("child")
    setup.clock.advance(seconds=4)
    setup.enforcer.used["child"] = 2
    setup.service.reconcile("child")
    assert setup.service.status("child")["countedLockedSeconds"] == 2
    setup.clock.advance(minutes=2)
    setup.service.reconcile("child")
    assert setup.service.status("child")["countedLockedSeconds"] == 2
    sessions.locked = False
    setup.clock.advance(seconds=3)
    setup.service.reconcile("child")
    assert setup.service.status("child")["countedLockedSeconds"] == 2


def test_failed_locked_charge_is_reconciled_without_double_charge(setup, monkeypatch):
    from types import SimpleNamespace

    setup.config.managedUsers["child"].countLockedTime = True
    setup.service.sessions = SimpleNamespace(all_locked=lambda user: True)
    setup.service.reconcile("child")
    setup.clock.advance(seconds=3)
    apply = setup.enforcer.apply

    def fail(*args):
        raise Unavailable("Failed after durable charge")

    monkeypatch.setattr(setup.enforcer, "apply", fail)
    assert not setup.service.reconcile("child")
    assert setup.repository.load("child", setup.clock.now()).locked_seconds == 3
    monkeypatch.setattr(setup.enforcer, "apply", apply)
    setup.service.reconcile("child")
    assert setup.service.status("child")["countedLockedSeconds"] == 3


def test_corrupt_user_state_does_not_stop_sibling_reconciliation(setup):
    setup.repository.db.execute("INSERT INTO state VALUES('child','policy_v2','broken')")
    assert not setup.service.reconcile("child")
    assert setup.service.reconcile("sibling")
    assert (
        setup.repository.db.execute("SELECT value FROM state WHERE user='child'").fetchone()[0]
        == "broken"
    )


def test_irrelevant_fields_are_rejected_before_writes(setup):
    with pytest.raises(InvalidCommand, match="Fields"):
        command(setup, "unlimited-today", {"minutes": 25})
    assert not setup.service.status("child")["unlimitedToday"]


def test_cutoff_reduction_also_clips_previously_granted_windows(setup):
    setup.clock.advance(hours=8)
    command(setup, "grant-session", {"minutes": 25})
    command(setup, "allow-until", {"until": "20:10"})
    assert setup.service.status("child")["allowedUntil"] == "20:10"


def test_allow_all_day_clears_cutoff_and_homework_restriction(setup):
    setup.config.managedUsers["child"].timeWindows.unmetered = {"ALL": ["12:00-13:00"]}
    command(setup, "allow-until", {"until": "17:00"})
    status = command(setup, "allow-today")
    assert status["allowedUntil"] == "24:00"
    assert not status["insideUnmeteredWindow"]
    assert command(setup, "restore-today")["insideUnmeteredWindow"]
