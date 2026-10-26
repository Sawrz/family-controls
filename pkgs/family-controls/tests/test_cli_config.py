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


def test_device_credential_generation_hash_and_no_overwrite(monkeypatch, tmp_path, capsys):
    import hashlib

    path = tmp_path / "device-token"
    command = ["ctl", "device-credential", "--output-token-file", str(path)]
    monkeypatch.setattr(sys, "argv", command)
    cli.main()
    output = capsys.readouterr().out
    token = secret(str(path))
    assert len(token) >= 43 and token not in output
    assert path.stat().st_mode & 0o777 == 0o600
    expected = {"credentialHash": hashlib.sha256(token.encode()).hexdigest()}
    assert json.loads(output) == expected
    with pytest.raises(SystemExit):
        cli.main()
    assert secret(str(path)) == token
    monkeypatch.setattr(
        sys, "argv", ["ctl", "device-credential-hash", "--input-token-file", str(path)]
    )
    cli.main()
    assert json.loads(capsys.readouterr().out) == expected


def test_declarative_client_reads_runtime_credentials_without_creating_state(setup, tmp_path):
    from family_controls.agent import device_client

    token = tmp_path / "token"
    token.write_text("first-token")
    setup.config.agent.credentialFile = str(token)
    setup.config.agent.backend = BackendConnection(url="https://backend")
    setup.config.agent.stateDir = str(tmp_path / "unused-state")
    client = device_client(setup.config)
    assert client.headers() == {"Authorization": "Bearer first-token"}
    token.write_text("rotated-token")
    assert client.headers() == {"Authorization": "Bearer rotated-token"}
    assert not (tmp_path / "unused-state").exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"credentialFile": ""},
        {"credentialFile": "/run/token"},
        {
            "credentialFile": "/run/token",
            "accounts": {"child": "child"},
            "enrollmentTokenFile": "/run/invite",
        },
    ],
)
def test_incomplete_declarative_credentials_cannot_fall_back_to_manual_enrollment(settings):
    from family_controls.config import AgentSettings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentSettings.model_validate(settings)
