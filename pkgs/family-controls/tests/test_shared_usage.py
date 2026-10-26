"""Conservation and reporting at the shared allowance boundary."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from family_controls.adapters.backend_sqlite import BackendStore
from family_controls.backend.usage import UsageLedger
from family_controls.domain import Conflict, InvalidCommand
from family_controls.usage import Usage

DAY = date(2026, 10, 26)


@pytest.fixture
def ledger(tmp_path):
    store = BackendStore(str(tmp_path / "ledger.sqlite"))
    yield UsageLedger(store, 300)
    store.close()


def exchange(ledger, device, report, limit=600, revision=1, today=DAY):
    with ledger.store.transaction():
        ledger.accept("child", device, report, today)
        return ledger.allocate("child", device, report, today, limit, revision)


def sample(**changes):
    return Usage(user="alex", day=DAY, sequence=1, policyRevision=1, needsTime=True, **changes)


def test_concurrent_devices_cannot_reserve_the_same_allowance(ledger):
    with ThreadPoolExecutor(max_workers=3) as pool:
        grants = list(pool.map(lambda d: exchange(ledger, d, sample(), limit=420), ["a", "b", "c"]))
    assert sum(g.issuedSeconds for g in grants) == 420
    assert ledger.balance("child", DAY, 420)["availableSeconds"] == 0


def test_lost_allocation_response_does_not_issue_duplicate_credit(ledger):
    report = sample()
    first = exchange(ledger, "a", report)
    retry = exchange(ledger, "a", report)
    assert retry.issuedSeconds == first.issuedSeconds == 300
    assert retry.reservedSeconds == 300


def test_simultaneous_usage_is_summed_and_reserved_credit_remains_unavailable(ledger):
    for device in ("a", "b"):
        exchange(ledger, device, sample())
    for device in ("a", "b"):
        report = sample(issuedSeconds=300, playSeconds=120)
        report.sequence = 2
        exchange(ledger, device, report)
    assert ledger.balance("child", DAY, 600) == {
        "reportedUsedSeconds": 240,
        "reservedSeconds": 360,
        "availableSeconds": 0,
        "limitSeconds": 600,
    }


def test_acknowledged_surrender_makes_credit_available_to_another_device(ledger):
    exchange(ledger, "a", sample(), limit=300)
    returned = sample(issuedSeconds=300, returnedSeconds=200, playSeconds=100)
    returned.sequence = 2
    with ledger.store.transaction():
        ledger.accept("child", "a", returned, DAY)
    other = exchange(ledger, "b", sample(), limit=300)
    assert other.issuedSeconds == 200
    assert other.reportedUsedSeconds == 100


@pytest.mark.parametrize(
    "field", ["playSeconds", "activeSeconds", "unmeteredSeconds", "returnedSeconds"]
)
def test_newer_snapshot_cannot_erase_reported_counters(ledger, field):
    exchange(ledger, "a", sample())
    earlier = sample(
        issuedSeconds=300, playSeconds=20, activeSeconds=50, unmeteredSeconds=10, returnedSeconds=20
    )
    earlier.sequence = 2
    with ledger.store.transaction():
        ledger.accept("child", "a", earlier, DAY)
    later = earlier.model_copy(update={field: 0, "sequence": 3})
    with pytest.raises(Conflict), ledger.store.transaction():
        ledger.accept("child", "a", later, DAY)


def test_late_previous_day_report_does_not_spend_todays_budget(ledger):
    exchange(ledger, "a", sample())
    report = sample(issuedSeconds=300, playSeconds=200)
    report.sequence = 2
    with ledger.store.transaction():
        ledger.accept("child", "a", report, DAY + timedelta(days=1))
    assert ledger.balance("child", DAY + timedelta(days=1), 600)["availableSeconds"] == 600
    assert ledger.week("child", DAY, DAY + timedelta(days=1), [])["days"][0]["playSeconds"] == 200


def test_missing_history_is_not_reported_as_zero(ledger):
    week = ledger.week("child", DAY - timedelta(days=2), DAY, [])
    assert week["days"][0]["availability"] == "missing"
    assert week["days"][0]["playSeconds"] is None
    assert week["days"][3]["availability"] == "future"


def test_reduction_cannot_reassign_an_offline_reservation(ledger):
    exchange(ledger, "offline", sample())
    result = exchange(ledger, "online", sample(), limit=60)
    assert result.issuedSeconds == 0
    assert result.reservedSeconds == 300


def test_policy_change_requires_device_acknowledgement_before_new_credit(ledger):
    assert exchange(ledger, "a", sample(), revision=2).issuedSeconds == 0


def test_unlimited_allocations_stop_at_the_daily_enforcement_cap(ledger):
    first = exchange(ledger, "a", sample(), limit=None)
    report = sample(issuedSeconds=first.issuedSeconds, playSeconds=600)
    report.sequence = 2
    second = exchange(ledger, "a", report, limit=None)
    assert second.issuedSeconds == 86400


def test_unissued_credit_and_future_usage_are_rejected(ledger):
    with pytest.raises(Conflict):
        exchange(ledger, "a", sample(issuedSeconds=10))
    with pytest.raises(InvalidCommand):
        exchange(ledger, "a", sample().model_copy(update={"day": DAY + timedelta(days=1)}))


def test_backend_restart_retains_offline_reservations(tmp_path):
    path = str(tmp_path / "restart.sqlite")
    store = BackendStore(path)
    exchange(UsageLedger(store), "a", sample(), limit=300)
    store.close()
    reopened = BackendStore(path)
    try:
        assert exchange(UsageLedger(reopened), "b", sample(), limit=300).issuedSeconds == 0
    finally:
        reopened.close()


def test_late_enforcement_overrun_after_surrender_stays_charged(ledger):
    first = sample()
    exchange(ledger, "a", first)
    report = sample(issuedSeconds=300, returnedSeconds=200, playSeconds=105)
    report.sequence = 2
    result = exchange(ledger, "a", report, limit=100)
    assert result.reportedUsedSeconds == 105
    assert result.availableSeconds == result.reservedSeconds == 0
