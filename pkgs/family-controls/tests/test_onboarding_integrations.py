"""First-run policies and self-service accounts across the real API boundary."""

import pytest
from family_controls.adapters.timekpr import validate_user
from family_controls.backend.models import ChildConfiguration, IntegrationMetadata
from family_controls.backend.service import Backend
from test_backend import agent
from test_backend import central as central


@pytest.fixture
def unfinished(central):
    policy = central.config.children["child"]
    central.config.children["child"] = ChildConfiguration(
        assignments=policy.assignments,
        timezone=policy.timezone,
    )
    with central.store.transaction():
        central.store.delete("policies", "child")
    Backend(central.config, central.store, central.setup.clock, validate_user)
    return central


def test_setup_required_blocks_grants_then_survives_restart_and_offline_cache(unfinished):
    c = unfinished
    sync = agent(c)
    sync.exchange()
    first = c.client.get("/api/v1/children/child/policy").json()
    assert first["setupComplete"] is False
    assert c.setup.service.status("child")["effectiveDailyLimitMinutes"] == 0
    assert (
        c.client.post(
            "/api/v1/children/child/commands/allow-today",
            json={},
            headers={"Idempotency-Key": "no-bypass"},
        ).status_code
        == 409
    )
    desired = {"dailyLimitMinutes": 80, "timeWindows": {"allowed": {"ALL": ["08:00-19:00"]}}}
    result = c.client.post("/api/v1/children/child/setup?revision=1", json=desired)
    assert result.status_code == 200 and result.json()["setupComplete"]
    assert c.client.post("/api/v1/children/child/setup?revision=1", json=desired).status_code == 409
    sync.exchange()
    assert c.setup.service.status("child")["effectiveDailyLimitMinutes"] == 80
    restarted = Backend(c.config, c.store, c.setup.clock, validate_user)
    assert restarted.policy("child")["policy"]["playTime"]["dailyLimitMinutes"] == 80
    assert restarted.policy("child")["setupComplete"]
    assert (
        c.setup.repository.relay_get("backend-policy")["users"]["child"]["playTime"][
            "dailyLimitMinutes"
        ]
        == 80
    )


def test_invalid_setup_does_not_mark_child_complete(unfinished):
    c = unfinished
    response = c.client.post(
        "/api/v1/children/child/setup?revision=1",
        json={
            "dailyLimitMinutes": 60,
            "timeWindows": {"allowed": {"ALL": ["09:00-09:15", "09:30-10:00"]}},
        },
    )
    assert response.status_code == 400
    assert not c.backend.policy("child")["setupComplete"]


def test_existing_policies_remain_completed_when_nix_seed_is_removed(central):
    original = central.backend.policy("child")
    central.config.children["child"] = ChildConfiguration(
        assignments=central.config.children["child"].assignments
    )
    restarted = Backend(central.config, central.store, central.setup.clock, validate_user)
    assert restarted.policy("child") == original
    assert restarted.policy("child")["setupComplete"]


def register_parent(c, name="parent"):
    c.client.post(
        "/api/v1/auth/register", json={"username": name, "password": "parent-test-password"}
    )
    c.client.post(f"/api/v1/accounts/{name}/approve", json={"role": "parent"})
    token = c.accounts.login(name, "parent-test-password")
    return {"Authorization": "Bearer " + token}


def test_personal_profile_password_sessions_and_admin_boundaries(central):
    c = central
    parent = register_parent(c)
    other = {"Authorization": "Bearer " + c.accounts.login("parent", "parent-test-password")}
    assert (
        c.client.put(
            "/api/v1/me/profile",
            json={"displayName": " Parent  Name ", "language": "de"},
            headers=parent,
        ).json()["displayName"]
        == "Parent Name"
    )
    for path in ("/api/v1/accounts", "/api/v1/integrations", "/api/v1/devices"):
        assert c.client.get(path, headers=parent).status_code == 403
    assert (
        c.client.post(
            "/api/v1/me/password",
            headers=parent,
            json={"currentPassword": "wrong", "newPassword": "a-new-long-password"},
        ).status_code
        == 401
    )
    assert (
        c.client.post(
            "/api/v1/me/password",
            headers=parent,
            json={"currentPassword": "parent-test-password", "newPassword": "a-new-long-password"},
        ).status_code
        == 200
    )
    assert c.client.get("/api/v1/me", headers=parent).status_code == 200
    assert c.client.get("/api/v1/me", headers=other).status_code == 401
    assert (
        c.client.post(
            "/api/v1/auth/login", json={"username": "parent", "password": "a-new-long-password"}
        ).status_code
        == 200
    )
    assert (
        c.client.put(
            "/api/v1/accounts/admin/role", headers=parent, json={"role": "parent"}
        ).status_code
        == 403
    )
    assert c.client.put("/api/v1/accounts/admin/role", json={"role": "parent"}).status_code == 409


def test_verified_link_is_two_step_private_and_cannot_take_over_another_parent(central):
    c = central
    parent = register_parent(c)
    other = register_parent(c, "other")
    adapter = {"Authorization": "Bearer test-telegram-token"}
    first = c.client.post("/api/v1/me/connections/telegram/challenge", headers=parent).json()
    assert (
        c.client.post(
            "/api/v1/adapter/link",
            headers=parent,
            json={"token": first["token"], "externalId": "7"},
        ).status_code
        == 403
    )
    assert (
        c.client.post(
            "/api/v1/adapter/link",
            headers=adapter,
            json={"token": first["token"], "externalId": "7", "label": "@parent"},
        ).status_code
        == 200
    )
    assert c.store.items("identities") == []
    personal = c.client.get("/api/v1/me/connections", headers=parent).json()
    assert first["token"] not in str(personal)
    claim = personal["integrations"][0]["challenge"]
    confirmation = {"challengeId": claim["challengeId"], "externalId": claim["externalId"]}
    assert (
        c.client.post(
            "/api/v1/me/connections/telegram/confirm", headers=other, json=confirmation
        ).status_code
        == 409
    )
    assert (
        c.client.post(
            "/api/v1/me/connections/telegram/confirm", headers=parent, json=confirmation
        ).status_code
        == 200
    )
    assert (
        c.client.get("/api/v1/me", headers={**adapter, "X-Family-Actor": "telegram:7"}).json()[
            "username"
        ]
        == "parent"
    )
    assert (
        c.client.get("/api/v1/me/connections", headers=other).json()["integrations"][0]["links"]
        == []
    )
    assert (
        c.client.delete("/api/v1/me/connections/telegram?external_id=7", headers=other).status_code
        == 403
    )
    second = c.client.post("/api/v1/me/connections/telegram/challenge", headers=other).json()
    c.client.post(
        "/api/v1/adapter/link", headers=adapter, json={"token": second["token"], "externalId": "7"}
    )
    assert (
        c.client.post(
            "/api/v1/me/connections/telegram/confirm",
            headers=other,
            json={"challengeId": second["challengeId"], "externalId": "7"},
        ).status_code
        == 409
    )
    assert (
        c.client.delete("/api/v1/me/connections/telegram?external_id=7", headers=parent).status_code
        == 200
    )
    assert (
        c.client.get("/api/v1/me", headers={**adapter, "X-Family-Actor": "telegram:7"}).status_code
        == 403
    )


def test_link_expiry_cancellation_and_adapter_cannot_manage_personal_settings(central):
    c = central
    adapter = {"Authorization": "Bearer test-telegram-token"}
    challenge = c.client.post("/api/v1/me/connections/telegram/challenge").json()
    c.setup.clock.advance(seconds=601)
    assert (
        c.client.post(
            "/api/v1/adapter/link",
            headers=adapter,
            json={"token": challenge["token"], "externalId": "8"},
        ).status_code
        == 400
    )
    assert c.client.get("/api/v1/me/connections").json()["integrations"][0]["challenge"] is None
    challenge = c.client.post("/api/v1/me/connections/telegram/challenge").json()
    c.client.delete("/api/v1/me/connections/telegram/challenge")
    assert (
        c.client.post(
            "/api/v1/adapter/link",
            headers=adapter,
            json={"token": challenge["token"], "externalId": "8"},
        ).status_code
        == 400
    )
    assert (
        c.client.put(
            "/api/v1/me/profile",
            headers={**adapter, "X-Family-Actor": "telegram:8"},
            json={"language": "en"},
        ).status_code
        == 403
    )
    assert c.client.post("/api/v1/me/connections/matrix/challenge").status_code == 400


def test_health_uses_observation_freshness_and_never_returns_credentials(central):
    c = central
    c.config.integrationMetadata["telegram"] = IntegrationMetadata(destination="-42")

    def overview():
        return c.client.get("/api/v1/integrations").json()["integrations"]

    assert overview()[0]["status"] == "unknown"
    assert overview()[1]["status"] == "not-configured"
    assert overview()[0]["destination"] == "-42"
    report = {"status": "connected", "bot": "@family", "destination": "Parents (-42)"}
    assert c.client.post("/api/v1/adapter/health", json=report).status_code == 403
    assert (
        c.client.post(
            "/api/v1/adapter/health",
            json=report,
            headers={"Authorization": "Bearer test-telegram-token"},
        ).status_code
        == 200
    )
    assert overview()[0]["status"] == "connected"
    success = overview()[0]["lastSuccess"]
    c.setup.clock.advance(seconds=91)
    assert overview()[0]["status"] == "unknown"
    c.client.post(
        "/api/v1/adapter/health",
        json={**report, "status": "problem"},
        headers={"Authorization": "Bearer test-telegram-token"},
    )
    assert overview()[0]["status"] == "problem" and overview()[0]["lastSuccess"] == success
    assert "test-telegram-token" not in str(overview()) and "tokenFile" not in str(overview())
