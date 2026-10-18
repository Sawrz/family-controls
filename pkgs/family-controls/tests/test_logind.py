from types import SimpleNamespace

import pytest
from family_controls.adapters import logind
from family_controls.domain import Unavailable


@pytest.mark.parametrize(
    "sessions,expected",
    [
        ([], False),
        ([("child", "x11", True)], True),
        ([("child", "wayland", True), ("child", "x11", False)], False),
        ([("child", "x11", True), ("sibling", "x11", False)], True),
        ([("child", "tty", True)], False),
    ],
)
def test_locked_state_requires_all_graphical_sessions_locked(monkeypatch, sessions, expected):
    closed = []

    def get_object(name, path, **kwargs):
        if path == "/org/freedesktop/login1":
            return SimpleNamespace(
                ListSessions=lambda **kw: [
                    (str(i), 1000, user, "", str(i)) for i, (user, _, _) in enumerate(sessions)
                ]
            )
        _, kind, locked = sessions[int(path)]
        return SimpleNamespace(GetAll=lambda *args, **kw: {"Type": kind, "LockedHint": locked})

    bus = SimpleNamespace(get_object=get_object, close=lambda: closed.append(True))
    dbus = SimpleNamespace(SystemBus=lambda **kwargs: bus, Interface=lambda obj, iface: obj)
    monkeypatch.setattr(logind.importlib, "import_module", lambda name: dbus)
    assert logind.Logind().all_locked("child") is expected
    assert closed == [True]


def test_session_transport_failure_is_not_unlocked(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("bus offline")

    monkeypatch.setattr(
        logind.importlib, "import_module", lambda name: SimpleNamespace(SystemBus=fail)
    )
    with pytest.raises(Unavailable, match="Session state"):
        logind.Logind().all_locked("child")
