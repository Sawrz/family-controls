import json
import sys

import pytest
from family_controls.adapters import cli
from family_controls.adapters.client import Gateway
from family_controls.bootstrap import SystemClock, stop_event
from family_controls.config import BackendConnection, secret
from family_controls.domain import Unavailable


def test_cli_local_request_encodes_optional_duration_and_reason(monkeypatch, capsys):
    calls = []

    def request(*args):
        calls.append(args)
        return {"accepted": True}

    monkeypatch.setattr(cli, "unix_request", request)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ctl",
            "--operation-id",
            "stable",
            "request-time",
            "--minutes",
            "25",
            "--reason",
            "Finish",
        ],
    )
    cli.main()
    assert calls[0][1:] == ("/v1/request-time", {"minutes": 25, "reason": "Finish"}, "stable")
    assert json.loads(capsys.readouterr().out)["accepted"]


def test_cli_remote_failure_prints_retry_identity(monkeypatch, tmp_path, capsys):
    token = tmp_path / "token"
    token.write_text("secret")
    calls = []

    def request(self, url, payload=None, **kwargs):
        kwargs["payload"] = payload
        calls.append((url, kwargs))
        raise Unavailable("Offline")

    monkeypatch.setattr(cli.BackendClient, "request", request)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ctl",
            "--backend",
            "https://localhost:443",
            "--token-file",
            str(token),
            "--operation-id",
            "retry",
            "allow-until",
            "child",
            "--until",
            "19:30",
        ],
    )
    with pytest.raises(SystemExit, match="1"):
        cli.main()
    assert calls[0][1]["payload"] == {"until": "19:30"}
    assert calls[0][1]["headers"]["Idempotency-Key"] == "retry"
    assert "retry" in capsys.readouterr().err


def test_gateway_uses_only_backend_and_preserves_operation_actor(monkeypatch):
    gateway = Gateway(BackendConnection(url="https://backend"))
    calls = []

    def request(route, payload=None, **kwargs):
        calls.append((route, payload, kwargs))
        return {}

    monkeypatch.setattr(gateway.client, "request", request)
    gateway.command("Kid", "add-time", {"minutes": 25}, "op", "telegram:7")
    assert calls == [
        (
            "/api/v1/children/Kid/commands/add-time",
            {"minutes": 25},
            {"headers": {"Idempotency-Key": "op", "X-Family-Actor": "telegram:7"}},
        )
    ]


@pytest.mark.parametrize("value", ["", "  ", "two words", "one\ntwo"])
def test_credentials_cannot_be_empty_or_contain_whitespace(tmp_path, value):
    path = tmp_path / "token"
    path.write_text(value)
    with pytest.raises(ValueError):
        secret(str(path))


def test_process_clock_and_shutdown_signals(monkeypatch):
    import signal

    callbacks = {}
    monkeypatch.setattr(
        signal, "signal", lambda signum, handler: callbacks.setdefault(signum, handler)
    )
    stop = stop_event()
    assert not stop.is_set()
    callbacks[signal.SIGTERM](signal.SIGTERM, None)
    assert stop.is_set()
    assert str(SystemClock("Europe/Berlin").now().tzinfo) == "Europe/Berlin"


def test_build_configuration_validation_does_not_open_runtime_services(
    setup, tmp_path, monkeypatch
):
    from family_controls.bootstrap import agent_main, relay_main

    agent = tmp_path / "agent.json"
    agent.write_text(setup.config.model_dump_json())
    monkeypatch.setattr(sys, "argv", ["agent", "--config", str(agent), "--check-config"])
    agent_main()
    relay = tmp_path / "relay.json"
    relay.write_text(
        json.dumps(
            {
                "parentGroupChatId": "-42",
                "children": ["child"],
                "backend": {"url": "https://backend", "tokenFile": "/unused"},
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["relay", "--config", str(relay), "--check-config"])
    relay_main()
    assert not (tmp_path / "agent.lock").exists()
    assert not (tmp_path / "relay.sqlite").exists()
