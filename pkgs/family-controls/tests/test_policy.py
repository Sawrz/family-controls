import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from family_controls.adapters.timekpr import hour_map, hours_string, render_user, validate_config
from family_controls.config import AgentConfig
from family_controls.domain import (
    InvalidCommand,
    Observation,
    Policy,
    State,
    Window,
    decide,
    duration,
    effective_policy,
    hhmm,
    midnight_after,
    minute,
    normalize,
    parse_duration,
)
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError


@pytest.mark.parametrize("value,expected", [("00:00", 0), ("23:59", 1439), ("24:00", 1440)])
def test_day_endpoints(value, expected):
    assert minute(value) == expected
    assert hhmm(expected * 60) == value


@pytest.mark.parametrize(
    "value", ["25:00", "24:01", "1:00", "09:60", "ab:cd", "１２:００", "-1:00", "01: 1"]
)
def test_invalid_time(value):
    with pytest.raises(InvalidCommand):
        minute(value)


@pytest.mark.parametrize("value", ["18:00-09:00", "09:00-09:00", "09:00", "24:00-24:00"])
def test_invalid_window(value):
    with pytest.raises(InvalidCommand):
        Window.parse(value)


@given(st.lists(st.tuples(st.integers(0, 1438), st.integers(1, 1440)), max_size=15))
def test_normalization_preserves_allowed_minutes(pairs):
    windows = [Window(start * 60, end * 60) for start, end in pairs if start < end]
    actual = normalize(windows)
    assert normalize(actual) == actual
    for minute_index in range(1440):
        assert any(w.contains(minute_index * 60) for w in windows) == any(
            w.contains(minute_index * 60) for w in actual
        )


def test_homework_is_intersection_not_extra_access():
    assert hour_map([Window.parse("09:00-10:00")], [Window.parse("11:00-12:00")]) == {
        "9": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0}
    }


@pytest.mark.parametrize(
    "allowed,homework", [(["09:00-09:15", "09:30-10:00"], []), (["09:00-10:00"], ["09:30-10:00"])]
)
def test_unrepresentable_timekpr_hours_are_rejected(allowed, homework):
    with pytest.raises(InvalidCommand, match="cannot represent"):
        hour_map([Window.parse(v) for v in allowed], [Window.parse(v) for v in homework])


def test_shared_renderer_supports_24_and_empty_schedule(setup):
    cfg = setup.config.managedUsers["child"]
    cfg.timeWindows.allowed = {"ALL": ["23:00-24:00"]}
    cfg.timeWindows.unmetered = {"ALL": ["23:00-24:00"]}
    assert "ALLOWED_HOURS_1 = !23" in render_user("child", cfg)
    assert hours_string(hour_map([], [])) == ""
    validate_config(setup.config)


def test_countdown_uses_next_window_not_daily_total(clock):
    clock.value = clock.value.replace(hour=9, minute=59)
    policy = Policy((Window.parse("09:00-10:00"), Window.parse("11:00-12:00")), (), 3600, False)
    result = decide(policy, Observation(3600, 3660, clock.now()), clock.now(), 300)
    assert result.remaining == 60
    assert result.phase == "countdown_active"


@pytest.mark.parametrize("left,phase", [(0, "expired"), (59, "countdown_active"), (600, "idle")])
def test_budget_limits_countdown(clock, left, phase):
    result = decide(
        Policy((Window(0, 86400),), (), 3600, False),
        Observation(left, 7200, clock.now()),
        clock.now(),
        300,
    )
    assert (result.remaining, result.phase) == (left, phase)


def test_unlimited_budget_still_respects_schedule(clock):
    policy = Policy((Window.parse("09:00-13:00"),), (), None, False)
    assert decide(policy, Observation(0, 7200, clock.now()), clock.now(), 300).remaining == 3600
    clock.advance(hours=2)
    assert (
        decide(policy, Observation(7200, 7200, clock.now()), clock.now(), 300).phase
        == "outside_schedule"
    )


def test_overrides_expire_without_erasing_block_or_mute(clock):
    state = State(
        day="2026-10-17",
        delta_seconds=300,
        unlimited=True,
        until=80000,
        blocked_until="2026-10-20",
        muted=True,
        extra_windows=[Window(0, 86400)],
    )
    actual = state.today(clock.now())
    assert actual.delta_seconds == 0 and not actual.unlimited and actual.until is None
    assert actual.extra_windows == [] and actual.muted and actual.blocked_until == "2026-10-20"


@pytest.mark.parametrize("date,hours", [(dt.date(2026, 3, 29), 23), (dt.date(2026, 10, 25), 25)])
def test_daily_expiry_is_local_midnight_across_dst(date, hours):
    now = dt.datetime.combine(date, dt.time(), ZoneInfo("Europe/Berlin"))
    assert (midnight_after(now).timestamp() - now.timestamp()) / 3600 == hours


def test_overlay_aware_time_and_empty_policy(clock):
    state = State(day=clock.now().date().isoformat(), until=19 * 3600)
    policy = effective_policy([], [], 3600, state, clock.now())
    assert policy.windows == (Window(0, 19 * 3600),)


@pytest.mark.parametrize("value,minutes", [("25", 25), ("25m", 25), ("1h 10m", 70), ("2h", 120)])
def test_custom_duration(value, minutes):
    assert parse_duration(value) == minutes


@pytest.mark.parametrize("value", ["-1", "0", "1.5h", "tomorrow", "25h", ""])
def test_bad_duration(value):
    with pytest.raises(InvalidCommand):
        parse_duration(value)


@pytest.mark.parametrize(
    "seconds,expected", [(None, "Unlimited"), (-1, "00:00"), (65, "01:05"), (3601, "01:00:01")]
)
def test_duration_display(seconds, expected):
    assert duration(seconds) == expected


@pytest.mark.parametrize(
    "update", [{"timezone": "invalid-zone"}, {"pollIntervalSeconds": 0}, {"managedUsers": {}}]
)
def test_config_rejects_invalid_settings(setup, update):
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({**setup.config.model_dump(), **update})
