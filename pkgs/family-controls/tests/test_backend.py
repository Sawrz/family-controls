"""Central workflows through the real API, SQLite and autonomous agent boundary."""

from types import SimpleNamespace

import pytest
from family_controls.adapters.api import create_app
from family_controls.adapters.backend_sqlite import BackendStore
from family_controls.adapters.oidc import OIDC
from family_controls.adapters.passwords import ArgonPasswords
from family_controls.adapters.timekpr import validate_user
from family_controls.agent import AgentSync
from family_controls.backend.accounts import Accounts
from family_controls.backend.models import BackendConfig
from family_controls.backend.service import Backend
from fastapi.testclient import TestClient


@pytest.fixture
def central(setup, tmp_path):
    password = tmp_path / "admin-password"
    password.write_text("test-password-at-least-twelve")
    interface = tmp_path / "interface-token"
    interface.write_text("test-telegram-token")
    config = BackendConfig.model_validate(
        {
            "publicUrl": "https://testserver",
            "authentication": {
                "bootstrapPasswordFile": str(password),
                "password": {"registration": True},
            },
            "children": {
                name: {
                    "assignments": [{"device": "device", "user": name}],
                    "timezone": "Europe/Berlin",
                    "policy": value.model_dump(),
                }
                for name, value in setup.config.managedUsers.items()
            },
            "interfaces": {"telegram": {"kind": "telegram", "tokenFile": str(interface)}},
        }
    )
    store = BackendStore(str(tmp_path / "backend.sqlite"))
    backend = Backend(config, store, setup.clock, validate_user)
    accounts = Accounts(config.authentication, store, ArgonPasswords(), setup.clock)
    app = create_app(backend, accounts, OIDC(config, store, setup.clock, accounts))
    with TestClient(app, base_url="https://testserver") as client:
        token = accounts.login("admin", password.read_text())
        client.headers["Authorization"] = "Bearer " + token
        yield SimpleNamespace(
            config=config,
            store=store,
            backend=backend,
            accounts=accounts,
            client=client,
            app=app,
            setup=setup,
        )
    store.close()


def enroll(central):
    c = central.client
    token = c.post("/api/v1/devices/device/invitation").json()["token"]
    response = c.post(
        "/api/v1/enroll",
        json={"device": "device", "token": token, "credential": "device-secret" * 4},
    )
    assert response.status_code == 202
    return token


def agent(central):
    enroll(central)
    assert central.client.post("/api/v1/devices/device/approve").status_code == 200

    class Client:
        def request(self, route, payload):
            response = central.client.post(
                route, json=payload, headers={"Authorization": "Bearer " + "device-secret" * 4}
            )
            assert response.status_code == 200, response.text
            return response.json()

    return AgentSync(central.setup.service, central.setup.repository, Client())


def test_offline_grant_pending_until_device_applies_and_duplicate_does_not_repeat(central):
    sync = agent(central)
    path = "/api/v1/children/child/commands/add-time"
    args = {"json": {"minutes": 25}, "headers": {"Idempotency-Key": "once"}}
    queued = central.client.post(path, **args)
    assert queued.status_code == 202 and queued.json()["operation"]["status"] == "queued"
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 60
    sync.exchange()
    sync.exchange()
    status = central.client.get("/api/v1/children/child/status").json()
    assert status["effectiveDailyLimitMinutes"] == 85 and status["delivery"]["pending"] == 0
    assert central.client.post(path, **args).json()["duplicate"]
    sync.exchange()
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 85
    assert (
        central.client.post(path, json={"minutes": 26}, headers=args["headers"]).status_code == 409
    )


def test_request_has_only_one_decision_across_interfaces(central):
    sync = agent(central)
    request = central.setup.service.execute(
        "child", "request-time", {"minutes": 20}, "request", "unix:123"
    )["pendingRequest"]
    sync.exchange()
    path = "/api/v1/children/child/commands/decide-request"
    body = {"requestId": request["id"], "decision": "grant-session", "minutes": 20}
    assert (
        central.client.post(path, json=body, headers={"Idempotency-Key": "parent-one"}).status_code
        == 202
    )
    assert (
        central.client.post(path, json=body, headers={"Idempotency-Key": "parent-two"}).status_code
        == 409
    )
    sync.exchange()
    sync.exchange()
    assert central.backend.status("child")["request"]["status"] == "approved"
    assert any(e["kind"] == "request.changed" for e in central.store.events("parents", 0))


def test_enrollment_needs_approval_and_revocation_takes_effect(central):
    enrollment_token = enroll(central)
    headers = {"Authorization": "Bearer " + "device-secret" * 4}
    body = {"policyRevision": 0}
    assert (
        central.client.post("/api/v1/devices/device/sync", json=body, headers=headers).status_code
        == 403
    )
    assert central.client.post("/api/v1/devices/device/approve").status_code == 200
    assert (
        central.client.post("/api/v1/devices/device/sync", json=body, headers=headers).status_code
        == 200
    )
    stolen = central.client.post(
        "/api/v1/enroll",
        json={"device": "device", "token": enrollment_token, "credential": "other-secret" * 4},
    )
    assert stolen.status_code == 401
    central.client.post("/api/v1/devices/device/revoke")
    assert (
        central.client.post("/api/v1/devices/device/sync", json=body, headers=headers).status_code
        == 403
    )


def test_pending_registration_cannot_access_children_until_approved(central):
    c = central.client
    registered = c.post(
        "/api/v1/auth/register", json={"username": "parent", "password": "another-test-password"}
    )
    assert registered.json()["status"] == "pending"
    assert (
        c.post(
            "/api/v1/auth/login", json={"username": "parent", "password": "another-test-password"}
        ).status_code
        == 403
    )
    assert c.post("/api/v1/accounts/parent/approve", json={"role": "parent"}).status_code == 200
    login = c.post(
        "/api/v1/auth/login", json={"username": "parent", "password": "another-test-password"}
    )
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    assert c.get("/api/v1/children", headers=headers).status_code == 200
    assert c.get("/api/v1/accounts", headers=headers).status_code == 403
    c.post("/api/v1/accounts/parent/revoke")
    assert c.get("/api/v1/children", headers=headers).status_code == 403


@pytest.mark.parametrize(
    "registration,login", [(False, False), (False, True), (True, False), (True, True)]
)
def test_password_switches_are_independent(central, registration, login):
    cfg = central.config.authentication.password
    cfg.registration, cfg.login = registration, login
    response = central.client.post(
        "/api/v1/auth/register", json={"username": "new", "password": "new-test-password"}
    )
    assert response.status_code == (201 if registration else 403)
    response = central.client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-at-least-twelve"},
    )
    assert response.status_code == (200 if login else 403)


def test_invitation_is_bound_single_use_and_can_preapprove(central):
    c = central.client
    token = c.post("/api/v1/invitations", json={"identity": "parent"}).json()["token"]
    bad = c.post(
        "/api/v1/auth/register",
        json={"username": "wrong", "password": "another-password", "invitation": token},
    )
    assert bad.status_code == 403
    good = c.post(
        "/api/v1/auth/register",
        json={"username": "parent", "password": "another-password", "invitation": token},
    )
    assert good.json()["status"] == "approved"
    assert central.store.get("invitations", token) is None


def test_interface_cannot_impersonate_unlinked_parent_or_another_protocol(central):
    c = central.client
    headers = {
        "Authorization": "Bearer test-telegram-token",
        "Idempotency-Key": "tg-op",
        "X-Family-Actor": "telegram:7",
    }
    path = "/api/v1/children/child/commands/add-time"
    assert c.post(path, json={"minutes": 5}, headers=headers).status_code == 403
    assert (
        c.post(
            "/api/v1/identities",
            json={"interface": "telegram", "externalId": "7", "account": "admin"},
        ).status_code
        == 200
    )
    assert c.post(path, json={"minutes": 5}, headers=headers).status_code == 202
    assert (
        c.post(
            path, json={"minutes": 5}, headers={**headers, "X-Family-Actor": "matrix:7"}
        ).status_code
        == 403
    )
    assert c.get("/api/v1/accounts", headers=headers).status_code == 403


def test_committed_daily_grant_remains_available_after_delayed_reconnect(central):
    sync = agent(central)
    central.client.post(
        "/api/v1/children/child/commands/add-time",
        json={"minutes": 5},
        headers={"Idempotency-Key": "expire"},
    )
    central.setup.clock.advance(minutes=11)
    sync.exchange()
    sync.exchange()
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 65
    assert central.store.get("commands", "expire")["status"] == "applied"


def test_stale_usage_becomes_unknown_and_device_cannot_report_other_users(central):
    sync = agent(central)
    sync.exchange()
    central.setup.clock.advance(seconds=31)
    assert central.backend.status("child")["remainingCountdownSeconds"] is None
    response = central.client.post(
        "/api/v1/devices/device/sync",
        json={"policyRevision": 0, "statuses": {"unassigned": {}}},
        headers={"Authorization": "Bearer " + "device-secret" * 4},
    )
    assert response.status_code == 403


def test_policy_edit_reaches_device_and_survives_local_cache(central):
    sync = agent(central)
    policy = central.client.get("/api/v1/children/child/policy").json()
    revision = policy.pop("revision")
    policy["policy"]["playTime"]["dailyLimitMinutes"] = 90
    assert (
        central.client.put("/api/v1/children/child/policy?revision=1", json=policy).status_code
        == 200
    )
    assert (
        central.client.put(
            f"/api/v1/children/child/policy?revision={revision}", json=policy
        ).status_code
        == 409
    )
    sync.exchange()
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 90
    assert (
        central.setup.repository.relay_get("backend-policy")["users"]["child"]["playTime"][
            "dailyLimitMinutes"
        ]
        == 90
    )


def test_cookie_mutation_requires_same_origin_and_revoking_last_admin_fails(central):
    c = central.client
    session = c.headers.pop("Authorization")[7:]
    c.cookies.set("family_session", session)
    assert c.post("/api/v1/devices/device/invitation").status_code == 403
    assert (
        c.post(
            "/api/v1/devices/device/invitation", headers={"Origin": "https://testserver"}
        ).status_code
        == 200
    )
    c.headers["Authorization"] = "Bearer " + session
    assert c.post("/api/v1/accounts/admin/revoke").status_code == 409


def test_event_cursors_are_independent_and_device_audiences_are_isolated(central):
    central.backend.command("child", "add-time", {"minutes": 5}, "stream", "admin")
    first = central.store.events("parents", 0)
    assert first and central.store.events("parents", 0) == first
    assert central.store.events("parents", first[-1]["id"]) == []
    assert central.store.events("device:device", 0)
    assert central.store.events("device:other", 0) == []


def test_crash_after_local_commit_before_acknowledgement_cannot_misreport_expiry(central):
    sync = agent(central)
    central.backend.command("child", "add-time", {"minutes": 5}, "lost-ack", "admin")
    # The process died after committing policy but before saving its outbound receipt.
    central.setup.service.execute("child", "add-time", {"minutes": 5}, "lost-ack", "admin")
    central.setup.clock.advance(minutes=11)
    sync.exchange()
    sync.exchange()
    assert central.store.get("commands", "lost-ack")["status"] == "applied"
    assert central.backend.status("child")["effectiveDailyLimitMinutes"] == 65


def test_invalid_recurring_policy_is_rejected_before_it_can_block_device_sync(central):
    policy = central.client.get("/api/v1/children/child/policy").json()
    policy.pop("revision")
    policy["policy"]["timeWindows"]["allowed"] = {"ALL": ["09:00-09:15", "09:30-10:00"]}
    response = central.client.put("/api/v1/children/child/policy?revision=1", json=policy)
    assert response.status_code == 400
    assert central.backend.policy("child")["revision"] == 1


def test_invalid_credentials_are_never_echoed_in_validation_errors(central):
    response = central.client.post(
        "/api/v1/auth/register", json={"username": "x", "password": "secret"}
    )
    assert response.status_code == 422
    assert "secret" not in response.text


def test_revocation_preserves_centrally_committed_budget_changes(central):
    sync = agent(central)
    central.client.post(
        "/api/v1/auth/register",
        json={
            "username": "parent",
            "password": "another-long-password",
        },
    )
    central.client.post("/api/v1/accounts/parent/approve", json={"role": "parent"})
    central.backend.command("child", "add-time", {"minutes": 10}, "committed", "parent")
    sync.exchange()
    central.backend.command("child", "add-time", {"minutes": 20}, "queued", "parent")
    central.accounts.revoke("parent", "admin")
    sync.exchange()
    sync.exchange()
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 90
    assert central.store.get("commands", "committed")["status"] == "applied"
    assert central.store.get("commands", "queued")["status"] == "applied"
    from family_controls.backend.accounts import Forbidden

    with pytest.raises(Forbidden):
        central.accounts.account("parent")


def test_boot_render_uses_cached_backend_policy_without_network(setup, tmp_path, monkeypatch):
    from family_controls.adapters.timekpr import render_main

    users = {key: value.model_dump() for key, value in setup.config.managedUsers.items()}
    users["child"]["playTime"]["dailyLimitMinutes"] = 42
    setup.repository.relay_set("backend-policy", {"revision": 2, "users": users})
    config = tmp_path / "config.json"
    config.write_text(setup.config.model_dump_json())
    directory = tmp_path / "timekpr"
    monkeypatch.setattr(
        "sys.argv", ["render", "--config", str(config), "--directory", str(directory)]
    )
    render_main()
    assert "2520" in (directory / "timekpr.child.conf").read_text()
    assert setup.service.status("child")["effectiveDailyLimitMinutes"] == 60


def test_cached_policy_is_not_acknowledged_until_enforcement_recovers(central):
    sync = agent(central)
    central.setup.enforcer.offline = True
    sync.exchange()
    sync.exchange()
    assert central.backend.status("child")["policyDelivery"] == "pending"
    central.setup.enforcer.offline = False
    sync.exchange()
    assert central.backend.status("child")["policyDelivery"] == "applied"


def test_missing_optional_interface_secret_does_not_block_administration(central):
    from pathlib import Path

    Path(central.config.interfaces["telegram"].tokenFile).unlink()
    assert central.client.get("/api/v1/devices").status_code == 200
    assert (
        central.client.get(
            "/api/v1/children",
            headers={"Authorization": "Bearer test-telegram-token"},
        ).status_code
        == 401
    )


@pytest.mark.parametrize("trusted", [True, False])
def test_private_socket_uses_kernel_identity_and_public_headers_cannot_spoof_it(
    central, tmp_path, trusted
):
    import os
    import pwd
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    from family_controls.adapters.local_backend import peer_protocol

    user = (
        pwd.getpwuid(os.getuid()).pw_name if trusted else ("nobody" if os.getuid() == 0 else "root")
    )
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = str(tmp_path / "adapter.sock")
    listener.bind(path)
    listener.listen(16)
    server = uvicorn.Server(
        uvicorn.Config(
            central.app,
            http=peer_protocol({user: "telegram"}),
            proxy_headers=False,
            log_level="error",
            timeout_graceful_shutdown=1,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=path, trust_env=False),
            base_url="http://localhost",
            trust_env=False,
        ) as client:
            if not trusted:
                with pytest.raises((httpx.RemoteProtocolError, httpx.ReadError)):
                    client.get("/api/v1/children")
                return
            assert client.get("/api/v1/children").status_code == 200
            assert client.get("/api/v1/accounts").status_code == 403
            assert (
                client.get("/api/v1/me", headers={"X-Family-Actor": "telegram:7"}).status_code
                == 403
            )
            central.accounts.link("telegram", "7", "admin", "admin")
            assert (
                client.get("/api/v1/me", headers={"X-Family-Actor": "telegram:7"}).json()[
                    "username"
                ]
                == "admin"
            )
            assert (
                client.get("/api/v1/me", headers={"X-Family-Actor": "matrix:7"}).status_code == 403
            )
        central.client.headers.pop("Authorization")
        assert (
            central.client.get(
                "/api/v1/children",
                headers={
                    "X-Forwarded-For": "unix-adapter:telegram",
                    "X-Family-Actor": "telegram:7",
                },
            ).status_code
            == 401
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_native_counter_reset_does_not_restore_consumed_shared_credit(central):
    sync = agent(central)
    sync.exchange()
    sync.exchange()
    central.setup.enforcer.used["child"] = 40
    sync.exchange()
    before = central.setup.service.usage.read("child")
    central.setup.enforcer.used["child"] = 0
    sync.exchange()
    after = central.setup.service.usage.read("child")
    assert after.playSeconds == before.playSeconds == 40
    assert (
        central.setup.enforcer.policies["child"].limit_seconds
        == after.issuedSeconds - after.returnedSeconds - 40
    )
    central.setup.enforcer.used["child"] = 10
    sync.exchange()
    assert central.backend.status("child")["reportedUsedSeconds"] == 50


def test_lost_accounting_state_is_rejected_without_reissuing_credit(central):
    sync = agent(central)
    sync.exchange()
    sync.exchange()
    value = central.setup.service.usage.read("child")
    key = central.backend.usage.key("child", "device", "child", value.day)
    before = central.store.get("allocations", key)
    central.setup.repository.relay_set(f"usage/child/{value.day}", None)
    central.setup.service.reconcile("child")
    report = central.setup.service.usage.read("child")
    response = central.client.post(
        "/api/v1/devices/device/sync",
        json={
            "policyRevision": 1,
            "usage": [report.model_dump(mode="json")],
        },
        headers={"Authorization": "Bearer " + "device-secret" * 4},
    )
    assert response.status_code == 409
    assert central.store.get("allocations", key) == before


def test_usage_sampling_preserves_subsecond_intervals_and_marks_restart_gap(central):
    sync = agent(central)
    sync.exchange()
    usage = central.setup.service.usage
    central.setup.service.sessions = SimpleNamespace(active=lambda user: True)
    for _ in range(11):
        central.setup.clock.advance(milliseconds=200)
        central.setup.service.reconcile("child")
    measured = usage.read("child")
    assert measured.activeSeconds == 2
    assert measured.observedSeconds == 2
    usage.samples.clear()
    central.setup.clock.advance(seconds=20)
    central.setup.service.reconcile("child")
    assert usage.read("child").unknownSeconds == 20


def test_surrender_keeps_unreported_consumed_time_reserved(central, monkeypatch):
    from family_controls.domain import Observation

    sync = agent(central)
    sync.exchange()
    sync.exchange()
    usage = central.setup.service.usage
    assert usage.read("child").issuedSeconds == 300
    original = central.setup.enforcer.observe

    def delayed_counter(user):
        if user != "child":
            return original(user)
        return Observation(
            250, 86400, central.setup.clock.now(), {"playSeconds": 0, "activeActivities": 0}
        )

    monkeypatch.setattr(central.setup.enforcer, "observe", delayed_counter)
    sync.exchange()
    value = usage.read("child")
    assert value.returnedSeconds == 220
    assert value.issuedSeconds - value.returnedSeconds == 80  # 50 unreported + 30 spendable


def test_delivery_acknowledges_commands_older_than_history_page(central):
    sync = agent(central)
    for index in range(105):
        central.backend.command("child", "set-limit", {"minutes": 60}, f"many-{index}", "admin")
    for _ in range(3):
        sync.exchange()
    assert len(central.backend.history("child")) == 100
    assert central.store.get("commands", "many-0")["status"] == "applied"
    assert central.store.pending_commands("child") == []


def test_assignment_changes_preserve_edited_policy_and_offline_reservations(central):
    from family_controls.backend.models import Assignment

    sync = agent(central)
    sync.exchange()
    sync.exchange()
    before = central.backend.status("child")["reservedSeconds"]
    central.backend.command("child", "set-limit", {"minutes": 80}, "shared-limit", "admin")
    central.config.children["child"].assignments = [Assignment(device="replacement", user="alex")]
    restarted = Backend(central.config, central.store, central.setup.clock, validate_user)
    status = restarted.status("child")
    assert status["effectiveDailyLimitMinutes"] == 80
    assert status["reservedSeconds"] == before
    assert status["devices"][0]["device"] == "replacement"


@pytest.mark.parametrize("action", ["request-time", "cancel-request"])
def test_child_request_changes_during_sync_are_not_overwritten(central, monkeypatch, action):
    sync = agent(central)
    sync.exchange()
    service = central.setup.service
    if action == "cancel-request":
        service.execute("child", "request-time", {"minutes": 15}, "first-request", "unix:123")
        sync.exchange()
    original = sync.client.request

    def in_flight(*args, **kwargs):
        result = original(*args, **kwargs)
        service.execute(
            "child",
            action,
            {"minutes": 15}
            if action == "request-time"
            else {"requestId": service.status("child")["request"]["id"]},
            "during-sync",
            "unix:123",
        )
        return result

    monkeypatch.setattr(sync.client, "request", in_flight)
    sync.exchange()
    monkeypatch.setattr(sync.client, "request", original)
    sync.exchange()
    assert central.backend.status("child")["request"]["status"] == (
        "pending" if action == "request-time" else "cancelled"
    )
