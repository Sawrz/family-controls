"""Warnings run locally and rearm only when an extension restores enough time."""

from family_controls.notifications import Warnings


def test_threshold_crossings_extension_and_midnight():
    warnings = Warnings()

    def observe(seconds, day="today"):
        return warnings.observe({"remainingCountdownSeconds": seconds, "overrideExpiresAt": day})

    assert observe(301) is None
    assert observe(299) == 300
    assert observe(298) is None
    assert observe(59) == 60
    assert observe(0) is None
    assert observe(900) is None
    assert observe(300) == 300
    assert observe(59, "tomorrow") == 60


def test_unknown_usage_does_not_create_or_consume_a_warning():
    warnings = Warnings()
    assert warnings.observe({"phase": "unknown", "remainingCountdownSeconds": 50}) is None
    assert warnings.observe({"remainingCountdownSeconds": None}) is None
    assert warnings.observe({"remainingCountdownSeconds": 50}) == 60
