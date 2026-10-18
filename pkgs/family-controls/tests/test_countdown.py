"""Real Tk widgets under Xvfb; deterministic futures isolate network scheduling."""

import os
from concurrent.futures import Future

import pytest

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Run with Xvfb for GUI checks"),
]


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        future = Future()
        self.calls.append((args, future))
        return future

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def app():
    from family_controls.adapters.countdown import CountdownApp

    window = CountdownApp("/unused")
    window.executor.shutdown()
    window.executor = Executor()
    yield window
    window.close()


def test_real_window_creation_close_and_reopen(app):
    status = {"phase": "countdown_active", "remainingCountdownSeconds": 59}
    app.render(status)
    app.root.update()
    assert app.window.winfo_class() == "family-controls-countdown"
    assert app.label_var.get() == "00:59"
    app.destroy_window()
    app.render(status)
    assert app.window.winfo_exists()
    app.render({"phase": "idle"})
    assert app.window is None


def test_request_during_inflight_poll_is_queued_and_ui_stays_responsive(app):
    app.render({"phase": "expired", "remainingCountdownSeconds": 0})
    app.tick()
    app.minutes_var.set("25m")
    app.request_button.invoke()
    assert app.queued[1]["minutes"] == 25
    app.root.update()  # An incomplete network future must not block Tk.
    app.future.set_result({"phase": "expired", "remainingCountdownSeconds": 0})
    app.tick()
    args, future = app.executor.calls[-1]
    assert args[1] == "/v1/request-time" and args[2]["minutes"] == 25
    assert app.request_button.instate(["disabled"])
    future.set_result(
        {"phase": "expired", "pendingRequest": {"id": "r"}, "request": {"status": "pending"}}
    )
    app.tick()
    assert app.cancel_button.instate(["!disabled"])
    app.cancel_button.invoke()
    assert app.queued[1] == {"requestId": "r"}


def test_invalid_duration_and_network_failure_are_visible(app):
    app.render({"phase": "expired"})
    app.minutes_var.set("-15")
    app.request_time()
    assert not app.queued and "Enter minutes" in app.feedback
    app.tick()
    app.future.set_exception(OSError("Device unavailable"))
    app.tick()
    assert app.label_var.get() == "Unavailable"
    assert "Device unavailable" in app.detail_var.get()
    app.render({"phase": "idle", "muted": True})
    assert app.request_button.instate(["disabled"])
