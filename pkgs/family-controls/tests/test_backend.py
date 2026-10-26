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
                    "device": "device",
                    "user": name,
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


def test_expired_grant_is_not_applied_after_reconnect(central):
    sync = agent(central)
    central.client.post(
        "/api/v1/children/child/commands/add-time",
        json={"minutes": 5},
        headers={"Idempotency-Key": "expire"},
    )
    central.setup.clock.advance(minutes=11)
    sync.exchange()
    sync.exchange()
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 60
    assert central.store.get("commands", "expire")["status"] == "expired"


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


def test_revocation_cancels_undelivered_commands_but_preserves_committed_receipts(central):
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
    assert central.setup.service.status("child")["effectiveDailyLimitMinutes"] == 70
    assert central.store.get("commands", "committed")["status"] == "applied"
    assert central.store.get("commands", "queued")["status"] == "failed"


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
