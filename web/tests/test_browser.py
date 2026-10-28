"""Parent workflows through HTTPS, the real API and device synchronization."""

import json
import os
import re
from pathlib import Path

import pytest
from family_controls.backend.models import Registration
from playwright.sync_api import expect


def child_row(page, name="mia"):
    return page.locator(".child-row").filter(has=page.get_by_role("heading", name=name[:1].upper() + name[1:], exact=True))


def apply_device(household):
    household.sync.exchange()
    household.sync.exchange()


def capture(page, name):
    destination = Path(os.environ.get("WEB_REVIEW_DIR", "test-results"))
    destination.mkdir(parents=True, exist_ok=True)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(destination / name), full_page=True)


def test_custom_grant_shared_accounts_and_confirmed_history(signed_in, household):
    page = signed_in
    expect(child_row(page).get_by_text("35 min", exact=True)).to_be_visible()
    expect(child_row(page, "jonas").get_by_text("Unknown", exact=True)).to_be_visible()
    page.get_by_label("Computers", exact=True).select_option("shared-pc")
    expect(page.locator(".child-row")).to_have_count(2)
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_label("Custom duration").fill("27")
    expect(page.locator(".change-preview")).to_contain_text("35 → 62 min")
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Waiting for computer")
    assert household.service.status("mia")["effectiveDailyLimitMinutes"] == 90
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(child_row(page).get_by_text("62 min", exact=True)).to_be_visible()
    assert household.service.status("lina")["effectiveDailyLimitMinutes"] == 90
    page.get_by_role("navigation").get_by_role("button", name="History", exact=True).click()
    expect(page.locator(".history-list")).to_contain_text("Applied")
    expect(page.locator(".history-list")).to_contain_text("Mia · Add play time")
    session = next(c for c in page.context.cookies() if c["name"] == "family_session")
    assert session["secure"] and session["httpOnly"] and session["sameSite"] == "Lax"
    assert not page.evaluate("Object.values(localStorage).some(v => v.includes('Bearer'))")


def test_uncertain_response_retry_after_reload_does_not_double_grant(signed_in, household):
    page = signed_in

    def lost_response(route):
        route.fetch()
        route.abort("connectionreset")

    page.route("**/commands/add-time", lost_response, times=1)
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(page.get_by_role("button", name="Retry the same change")).to_be_visible()
    apply_device(household)
    page.reload()
    page.get_by_role("button", name="Retry the same change").click()
    expect(page.get_by_role("button", name="Retry the same change")).to_have_count(0)
    apply_device(household)
    assert household.service.status("mia")["effectiveDailyLimitMinutes"] == 110
    assert len(household.backend.history("mia")) == 1


def test_allowance_preview_distinguishes_used_time_and_unknown_reports(signed_in, household):
    page = signed_in
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_label("Change", exact=True).select_option("set-limit")
    page.get_by_label("Custom duration").fill("75")
    preview = page.locator(".change-preview")
    expect(preview).to_contain_text("90 min → 75 min")
    expect(preview).to_contain_text("35 → 20 min")
    expect(preview).to_contain_text("includes time already used")
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "allowance-mobile.png")
    page.get_by_label("Change", exact=True).select_option("deduct-time")
    expect(preview).to_contain_text("90 min → 55 min")
    expect(preview).to_contain_text("35 → 0 min")
    page.get_by_role("button", name="Close", exact=True).click()
    child_row(page, "jonas").get_by_role("button", name="Change time").click()
    page.get_by_label("Change", exact=True).select_option("set-limit")
    expect(preview).to_contain_text("Unknown → 20 min")
    expect(preview).to_contain_text("Unknown → Unknown")
    expect(preview).to_contain_text("cannot be calculated")


def test_change_scope_tracks_operation_and_block_duration(signed_in):
    page = signed_in
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_label("Change", exact=True).select_option("mute")
    expect(page.get_by_role("heading", name="Persistent change")).to_be_visible()
    page.get_by_label("Change", exact=True).select_option("block")
    expect(page.get_by_role("heading", name="Today-only change")).to_be_visible()
    page.get_by_label("Block duration", exact=True).select_option("days")
    expect(page.get_by_role("heading", name="Block for selected days")).to_be_visible()
    expect(page.get_by_label("Block duration", exact=True).locator("option")).to_have_count(2)
    page.get_by_label("Change", exact=True).select_option("unmute")
    page.get_by_label("Language", exact=True).select_option("de")
    expect(page.get_by_role("heading", name="Dauerhafte Änderung")).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "persistent-mobile.png")


def test_set_remaining_from_overdrawn_budget_matches_preview(signed_in, household):
    page = signed_in
    household.service.enforcer.used["mia"] = 174 * 60
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(child_row(page).get_by_text("0 min", exact=True)).to_be_visible()
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_label("Change", exact=True).select_option("set-remaining")
    page.get_by_label("Custom duration").fill("90")
    preview = page.locator(".change-preview")
    expect(preview).to_contain_text("90 min → 264 min")
    expect(preview).to_contain_text("0 → 90 min")
    capture(page, "remaining-overdrawn-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "remaining-overdrawn-mobile.png")
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Waiting for computer")
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(child_row(page).get_by_text("90 min", exact=True)).to_be_visible()
    status = household.backend.status("mia")
    assert status["reportedUsedSeconds"] == 174 * 60
    assert status["remainingPlayTimeSeconds"] == 90 * 60


def test_successful_change_refreshes_again_after_an_older_snapshot(signed_in, household):
    page = signed_in
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_label("Change", exact=True).select_option("set-remaining")
    page.get_by_label("Custom duration").fill("90")
    held = []

    def hold_snapshot(route):
        held.append((route, route.fetch()))

    page.route("**/children/mia/status", hold_snapshot, times=1)
    with page.expect_request("**/children/mia/status"):
        page.get_by_role("button", name="Refresh", exact=True).click()
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Waiting for computer")
    assert held
    route, old_response = held[0]
    route.fulfill(response=old_response)
    # No manual refresh or ten-second polling tick is needed after the mutation.
    expect(child_row(page).get_by_text("90 min", exact=True)).to_be_visible(timeout=5000)
    # Another remaining-time edit waits for the device to acknowledge this policy.
    expect(page.get_by_role("button", name="Apply change", exact=True)).to_be_disabled()


def test_request_decision_with_custom_duration(signed_in, household):
    page = signed_in
    household.service.execute(
        "mia", "request-time", {"minutes": 20, "reason": "Finish project"}, "req-1", "unix:1000"
    )
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator(".request-banner").get_by_role("button", name="Review request").click()
    page.get_by_label("Custom duration").fill("17")
    page.get_by_label("Reason (optional)").fill("Finish this round")
    page.get_by_role("button", name="Send decision").click()
    apply_device(household)
    page.get_by_role("navigation").get_by_role("button", name=re.compile("Requests")).click()
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.get_by_text("No requests waiting.", exact=False)).to_be_visible()
    assert household.service.status("mia")["effectiveDailyLimitMinutes"] == 107


def test_policy_conflict_preserves_draft_then_explicit_reload(signed_in, household):
    page = signed_in
    page.get_by_role("navigation").get_by_role("button", name="Schedules").click()
    page.get_by_label("Account", exact=True).select_option("mia")
    limit = page.get_by_label("Daily allowance (minutes)", exact=True)
    limit.fill("120")
    policy = household.api.get("/api/v1/children/mia/policy").json()
    revision = policy.pop("revision")
    policy.pop("setupComplete")
    policy["policy"]["playTime"]["dailyLimitMinutes"] = 80
    assert (
        household.api.put(
            f"/api/v1/children/mia/policy?revision={revision}", json=policy
        ).status_code
        == 200
    )
    page.get_by_role("button", name="Save routine").click()
    expect(page.get_by_role("alert")).to_contain_text("Your draft is preserved")
    expect(limit).to_have_value("120")
    page.get_by_role("button", name="Discard draft and reload").click()
    expect(limit).to_have_value("80")
    limit.fill("100")
    page.get_by_label("Allowed hours", exact=True).first.fill("09:00-18:00")
    page.get_by_role("button", name="Save routine").click()
    expect(page.get_by_role("status")).to_contain_text("Saved")
    result = household.backend.policy("mia")
    assert result["policy"]["playTime"]["dailyLimitMinutes"] == 100
    assert result["policy"]["timeWindows"]["allowed"]["1"] == ["09:00-18:00"]


def test_german_mobile_filter_and_broad_action_confirmation(signed_in, household):
    page = signed_in
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Language", exact=True).select_option("de")
    expect(page.get_by_role("heading", name="Der Familienplan für heute")).to_be_visible()
    page.get_by_label("Kind oder Computer suchen").fill("mia")
    expect(page.locator(".child-row")).to_have_count(1)
    child_row(page).get_by_role("button", name="Zeit ändern").click()
    page.get_by_label("Änderung", exact=True).select_option("allow-today")
    expect(page.get_by_role("button", name="Änderung anwenden")).to_be_disabled()
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Änderung anwenden").click()
    expect(page.get_by_role("status")).to_contain_text("Wartet auf den Computer")
    apply_device(household)
    assert household.service.status("mia")["unlimitedToday"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_admin_approval_invitation_identity_and_device_enrollment(signed_in, household):
    household.accounts.register(Registration(username="parent", password="another-test-password"))
    page = signed_in
    page.get_by_role("navigation", name="Family Controls").get_by_role(
        "button", name="Household administration"
    ).click()
    row = page.locator(".record-list li").filter(has=page.get_by_text("parent", exact=True))
    row.get_by_role("button", name="Approve", exact=True).click()
    expect(row).to_contain_text("Approved")
    invitation = page.locator("form").filter(
        has=page.get_by_role("heading", name="Invite with password sign-in", exact=True)
    )
    invitation.get_by_label("Username").fill("invited-parent")
    invitation.get_by_role("button", name="Create invitation").click()
    expect(page.get_by_label("Copy this token now")).not_to_be_empty()
    page.get_by_role("button", name="Hide token").click()
    page.get_by_role("navigation", name="Household settings").get_by_role(
        "button", name="Devices", exact=True
    ).click()
    enrollment = page.locator("form").filter(
        has=page.get_by_role("heading", name="Create device enrollment token")
    )
    enrollment.get_by_label("Computers", exact=True).select_option("jonas-pc")
    enrollment.get_by_role("button", name="Create device enrollment token").click()
    expect(page.get_by_label("Copy this token now")).not_to_be_empty()
    token = page.get_by_label("Copy this token now").input_value()
    response = household.api.post(
        "/api/v1/enroll",
        json={"device": "jonas-pc", "token": token, "credential": "new-device-credential" * 3},
    )
    assert response.status_code == 202
    page.reload()
    row = page.locator(".record-list li").filter(has_text="jonas-pc")
    row.get_by_role("button", name="Approve", exact=True).click()
    expect(row).to_contain_text("Approved")
    row.get_by_role("button", name="Revoke access").click()
    page.locator(".alert.warning").get_by_role("button", name="Confirm change").click()
    expect(row).to_contain_text("Revoked")


@pytest.mark.parametrize(
    "password_login,password_register", [(False, False), (False, True), (True, False)]
)
def test_authentication_options_match_config(page, household, password_login, password_register):
    household.metadata["password"] = {"login": password_login, "registration": password_register}
    household.runtime.write_text(json.dumps(household.metadata))
    household.config.authentication.password.login = password_login
    household.config.authentication.password.registration = password_register
    page.goto(household.origin)
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_have_count(
        int(password_login)
    )
    if password_register:
        page.get_by_label("Username", exact=True).fill("new-parent")
        page.get_by_label("Password", exact=True).fill("new-parent-password")
        page.get_by_role("button", name="Create account", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("waiting for administrator approval")
    elif not password_login:
        expect(page.get_by_text("Sign-in is disabled.", exact=False)).to_be_visible()


@pytest.mark.parametrize(
    "action,field,value",
    [
        ("set-limit", "Custom duration", "0"),
        ("set-remaining", "Custom duration", "0"),
        ("deduct-time", "Custom duration", "5"),
        ("grant-session", "Custom duration", "15"),
        ("extend-access", "Custom duration", "5"),
        ("reduce-access", "Custom duration", "5"),
        ("allow-until", "Until (computer’s local time)", "23:00"),
        ("unlimited-today", None, None),
        ("restore-today", None, None),
        ("block", None, None),
        ("unblock", None, None),
        ("mute", None, None),
        ("unmute", None, None),
    ],
)
def test_each_control_sends_its_valid_contract(signed_in, household, action, field, value):
    page = signed_in
    child_row(page).get_by_role("button", name="Change time").click()
    page.get_by_label("Change", exact=True).select_option(action)
    if field:
        page.get_by_label(field, exact=True).fill(value)
    if page.get_by_role("checkbox").count():
        page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Waiting for computer")
    commands = household.store.items("commands")
    assert len(commands) == 1
    assert commands[0][1]["action"] == action
    if field == "Custom duration":
        assert commands[0][1]["payload"]["minutes"] == int(value)
    if action == "block":
        assert commands[0][1]["payload"]["today"] is True


def test_competing_request_decision_is_reported_without_a_second_grant(signed_in, household):
    page = signed_in
    pending = household.service.execute(
        "mia", "request-time", {"minutes": 20}, "competing-request", "unix:1000"
    )["pendingRequest"]
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator(".request-banner").get_by_role("button", name="Review request").click()
    response = household.api.post(
        "/api/v1/children/mia/commands/decide-request",
        headers={"Idempotency-Key": "other-parent"},
        json={"requestId": pending["id"], "decision": "add-time", "minutes": 10},
    )
    assert response.status_code == 202
    # Stop snapshots so this deliberately stale editor exercises the API conflict path.
    page.route("**/children/*/status", lambda route: route.abort())
    page.get_by_role("button", name="Send decision").click()
    expect(page.get_by_role("alert")).to_contain_text("The data changed")
    apply_device(household)
    assert household.service.status("mia")["effectiveDailyLimitMinutes"] == 100


@pytest.mark.parametrize("storage_blocked", [False, True])
def test_oidc_callback_returns_to_app_and_consumes_path_scoped_cookie(page, household, monkeypatch, storage_blocked):
    if storage_blocked:
        page.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('Blocked', 'SecurityError'); }});")
    household.metadata["oidc"] = {"login": True, "registration": False}
    household.metadata["providers"] = [{"id": "test", "label": "Test identity"}]
    household.runtime.write_text(json.dumps(household.metadata))

    def start(provider, invitation=None):
        return household.origin + "/api/v1/auth/oidc/test/callback?state=state&code=code", "binding"

    def finish(provider, state, code, binding):
        assert (provider, state, code, binding) == ("test", "state", "code", "binding")
        account = household.store.get("accounts", "admin")
        account["displayName"] = "Alex Müller"
        household.store.put("accounts", "admin", account)
        return account

    monkeypatch.setattr(household.oidc, "start", start)
    monkeypatch.setattr(household.oidc, "finish", finish)
    page.goto(household.origin)
    page.get_by_role("link", name="Continue with Test identity").click()
    page.get_by_role("heading", name="Today’s family plan").wait_for()
    assert "code=" not in page.url
    assert all(cookie["name"] != "family_oidc" for cookie in page.context.cookies())
    expect(page.locator(".rail-footer strong")).to_have_text("Alex Müller")
    page.get_by_role("navigation", name="Family Controls").get_by_role(
        "button", name="Household administration", exact=True
    ).click()
    expect(page.locator(".record-list strong").filter(has_text="Alex Müller")).to_be_visible()
    capture(page, "oidc-name-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "oidc-name-phone.png")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_registration_denial_logout_and_parent_permissions(page, household):
    page.goto(household.origin)
    page.get_by_label("Username", exact=True).fill("admin")
    page.get_by_label("Password", exact=True).fill("wrong-password")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Sign-in failed")
    household.accounts.register(Registration(username="parent", password="parent-long-password"))
    page.get_by_label("Username", exact=True).fill("parent")
    page.get_by_label("Password", exact=True).fill("parent-long-password")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("needs approval")
    household.accounts.approve("parent", "parent", "admin")
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("heading", name="Today’s family plan").wait_for()
    page.get_by_role("navigation").get_by_role("button", name="My account").click()
    expect(page.get_by_role("heading", name="Household administration")).to_have_count(0)
    assert page.evaluate("async () => (await fetch('/api/v1/accounts')).status") == 403
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
    assert not any(c["name"] == "family_session" for c in page.context.cookies())


def test_transport_failure_hides_previous_usage_and_recovers(signed_in):
    page = signed_in
    page.route("**/children/*/status", lambda route: route.abort())
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Connection lost")
    expect(child_row(page).get_by_text("Unknown", exact=True)).to_be_visible()
    page.unroute("**/children/*/status")
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(child_row(page).get_by_text("35 min", exact=True)).to_be_visible()


def test_policy_validation_error_keeps_input_and_advanced_settings(signed_in, household):
    page = signed_in
    page.get_by_role("navigation").get_by_role("button", name="Schedules").click()
    page.get_by_label("Account", exact=True).select_option("mia")
    field = page.get_by_label("Allowed hours", exact=True).first
    field.fill("nonsense")
    page.get_by_role("button", name="Save routine").click()
    expect(page.get_by_role("alert")).to_contain_text("Check the entered values")
    expect(field).to_have_value("nonsense")
    field.fill("09:00-18:00")
    page.get_by_text("Advanced enforcement settings", exact=True).click()
    page.get_by_label("Tracked process names (one per line)", exact=True).fill("game-one\ngame-two")
    page.get_by_label("Count inactive time", exact=True).check()
    page.get_by_label("Action when time runs out", exact=True).select_option("lock")
    page.get_by_role("button", name="Save routine").click()
    expect(page.get_by_role("status")).to_contain_text("Saved")
    policy = household.backend.policy("mia")["policy"]
    assert policy["playTime"]["activities"] == ["game-one", "game-two"]
    assert policy["trackInactive"] and policy["lockoutAction"] == "lock"


def test_security_headers_and_rejected_cross_origin_cookie_write(signed_in, household):
    page = signed_in
    response = household.api.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src" in response.text and "sha256-" in response.text
    cookie = next(c["value"] for c in page.context.cookies() if c["name"] == "family_session")
    response = household.api.post(
        "/api/v1/children/mia/commands/add-time",
        headers={
            "Authorization": "",
            "Cookie": f"family_session={cookie}",
            "Origin": "https://other.example",
            "Idempotency-Key": "cross-origin",
        },
        json={"minutes": 99},
    )
    assert response.status_code == 403
    assert not household.backend.history("mia")


def test_accessibility_and_responsive_views(signed_in, household):
    import os
    from pathlib import Path

    page = signed_in
    web = Path(__file__).resolve().parents[1]
    axe = Path(
        os.environ.get("AXE_SCRIPT", str(web / "node_modules/axe-core/axe.min.js"))
    ).read_text()
    screenshots = Path(os.environ.get("WEB_REVIEW_DIR", str(web / "test-results")))
    screenshots.mkdir(parents=True, exist_ok=True)

    def audit():
        page.evaluate(axe)
        result = page.evaluate("""async () => await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa']}})""")
        assert not result["violations"], [
            (v["id"], [n["target"] for n in v["nodes"]]) for v in result["violations"]
        ]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    household.service.execute(
        "mia",
        "request-time",
        {"minutes": 20, "reason": "Finish my project"},
        "review-request",
        "unix:1000",
    )
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator(".request-banner").wait_for()
    child_row(page).get_by_role("button", name="Change time").click()
    audit()
    page.screenshot(path=str(screenshots / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Language", exact=True).select_option("de")
    audit()
    page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
    page.get_by_role("button", name="Schließen", exact=True).click()
    page.get_by_role("navigation").get_by_role("button", name="Anfragen").click()
    page.get_by_role("button", name="Anfrage prüfen").click()
    page.get_by_label("Entscheidung", exact=True).select_option("deny")
    audit()
    page.get_by_role("button", name="Entscheidung senden").click()
    expect(page.locator(".alert[role=status]")).to_contain_text("Wartet")
    apply_device(household)
    page.get_by_role("navigation").get_by_role("button", name="Zeitpläne").click()
    page.get_by_label("Konto", exact=True).select_option("mia")
    audit()
    page.screenshot(path=str(screenshots / "schedule-mobile.png"), full_page=True)
    page.get_by_role("navigation").get_by_role("button", name="Mein Konto").click()
    page.get_by_role("heading", name="Mein Konto", exact=True).wait_for()
    audit()
    page.set_viewport_size({"width": 320, "height": 800})
    audit()
    page.get_by_role("button", name="Abmelden").click()
    expect(page.get_by_role("button", name="Anmelden", exact=True)).to_be_visible()
    audit()
    page.screenshot(path=str(screenshots / "login-mobile.png"), full_page=True)


def test_weekly_usage_distinguishes_metrics_missing_days_and_device_detail(signed_in, household):
    page = signed_in
    page.get_by_role("navigation").get_by_role("button", name="Weekly usage", exact=True).click()
    page.get_by_label("Account", exact=True).select_option("mia")
    expect(page.locator(".usage-days")).to_contain_text("55 min Metered play time")
    expect(page.locator(".usage-days")).to_contain_text("Active computer time: —")
    expect(page.locator(".usage-days")).to_contain_text("incomplete coverage")
    details = page.locator(".usage-days details").first
    details.get_by_text("Computer details", exact=True).click()
    expect(details).to_contain_text("shared-pc · mia")
    capture(page, "weekly-desktop.png")
    page.get_by_role("button", name="Previous week").click()
    expect(page.locator(".usage-days")).to_contain_text("No usage report")
    expect(page.locator(".usage-days progress")).to_have_count(0)
    page.get_by_role("button", name="Next week").click()
    expect(page.locator(".usage-days")).to_contain_text("55 min Metered play time")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Language", exact=True).select_option("de")
    expect(page.get_by_role("heading", name="Wöchentliche Nutzung")).to_be_visible()
    capture(page, "weekly-mobile-de.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_weekly_refresh_preserves_selection_and_recovers_from_failure(signed_in, household):
    page = signed_in
    page.get_by_role("navigation").get_by_role("button", name="Weekly usage", exact=True).click()
    page.get_by_label("Account", exact=True).select_option("mia")
    expect(page.locator(".usage-days")).to_contain_text("55 min Metered play time")
    household.service.enforcer.used["mia"] = 56 * 60
    apply_device(household)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator(".usage-days")).to_contain_text("56 min Metered play time")
    expect(page.get_by_label("Account", exact=True)).to_have_value("mia")
    capture(page, "weekly-refreshed.png")
    page.route(
        "**/children/mia/usage*", lambda route: route.fulfill(status=503, json={"error": "offline"})
    )
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator("section[aria-busy] [role=alert]")).to_be_visible()
    capture(page, "weekly-error.png")
    page.unroute("**/children/mia/usage*")
    page.locator("section[aria-busy]").get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator(".usage-days")).to_contain_text("56 min Metered play time")


def test_weekly_removed_child_and_empty_household_clear_obsolete_reports(signed_in):
    page = signed_in
    page.get_by_role("navigation").get_by_role("button", name="Weekly usage", exact=True).click()
    page.get_by_label("Account", exact=True).select_option("mia")
    page.route("**/api/v1/children", lambda route: route.fulfill(json={"children": ["lina"]}))
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.get_by_label("Account", exact=True)).to_have_value("lina")
    page.unroute("**/api/v1/children")
    page.route("**/api/v1/children", lambda route: route.fulfill(json={"children": []}))
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.get_by_text("No children are configured.", exact=False)).to_be_visible()
    expect(page.locator(".usage-days")).to_have_count(0)
    expect(page.get_by_label("Account", exact=True)).to_have_count(0)
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "weekly-empty-mobile.png")


def require_child_setup(household, *names):
    from family_controls.adapters.timekpr import validate_user
    from family_controls.backend.models import ChildConfiguration
    from family_controls.backend.service import Backend

    for name in names:
        household.config.children[name] = ChildConfiguration(
            assignments=household.config.children[name].assignments
        )
        with household.store.transaction():
            household.store.delete("policies", name)
    Backend(household.config, household.store, household.backend.clock, validate_user)


def test_child_setup_copies_policy_once_and_survives_reload(signed_in, household):
    require_child_setup(household, "mia")
    page = signed_in
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    child_row(page).get_by_role("button", name="Set up limits", exact=True).click()
    setup = page.get_by_role("region", name="Set up limits Mia")
    expect(setup.get_by_role("heading")).to_be_focused()
    page.set_viewport_size({"width": 1440, "height": 1000})
    expect(setup).to_contain_text("Access stays restricted")
    setup.get_by_role("button", name="Lina", exact=True).click()
    setup.get_by_label("Daily allowance in minutes").fill("85")
    capture(page, "child-setup-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "child-setup-phone.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 1440, "height": 1000})
    setup.get_by_role("button", name="Confirm limits", exact=True).click()
    expect(setup).to_have_count(0)
    assert household.backend.policy("mia")["setupComplete"]
    assert household.backend.policy("mia")["policy"]["playTime"]["dailyLimitMinutes"] == 85
    apply_device(household)
    page.reload()
    expect(child_row(page).get_by_role("button", name="Change time", exact=True)).to_be_visible()
    expect(child_row(page).get_by_role("button", name="Set up limits", exact=True)).to_have_count(0)
    assert household.service.status("mia")["effectiveDailyLimitMinutes"] == 85


def test_setup_switching_targets_and_reloading_conflicts_preserves_correct_draft(signed_in, household):
    require_child_setup(household, "mia", "lina")
    page = signed_in
    page.reload()
    page.get_by_role("navigation", name="Family Controls").get_by_role(
        "button", name="Household administration", exact=True
    ).click()
    page.get_by_role("navigation", name="Household settings").get_by_role(
        "button", name="Children", exact=True
    ).click()
    for name, minutes in (("mia", "110"), ("lina", "75")):
        page.locator(".record-list li").filter(
            has=page.get_by_text(name.capitalize(), exact=True)
        ).get_by_role("button", name="Set up limits", exact=True).click()
        setup = page.get_by_role("region", name=f"Set up limits {name.capitalize()}")
        expect(setup.get_by_role("heading")).to_be_focused()
        expect(setup.get_by_label("Daily allowance in minutes")).to_have_value("60")
        setup.get_by_label("Daily allowance in minutes").fill(minutes)
    # A deployment may change assignments/revision while this draft is open.
    with household.store.transaction():
        record = household.store.get("policies", "lina")
        record["revision"] += 1
        household.store.put("policies", "lina", record)
    confirm = setup.get_by_role("button", name="Confirm limits", exact=True)
    confirm.click()
    expect(confirm).to_be_disabled()
    setup.get_by_role("button", name="Reload current settings; keep draft").click()
    expect(confirm).to_be_enabled()
    expect(setup.get_by_label("Daily allowance in minutes")).to_have_value("75")
    confirm.click()
    expect(setup).to_have_count(0)
    assert household.backend.policy("lina")["policy"]["playTime"]["dailyLimitMinutes"] == 75
    assert not household.backend.policy("mia")["setupComplete"]


def test_personal_profile_and_verified_connections_are_separate_from_household(
    signed_in, household, tmp_path
):
    from family_controls.backend.models import InterfaceCredential

    credential = tmp_path / "adapter-token"
    credential.write_text("isolated-linking-adapter-token")
    household.config.interfaces["telegram"] = InterfaceCredential(
        kind="telegram", tokenFile=str(credential)
    )
    adapter = {"Authorization": "Bearer isolated-linking-adapter-token"}
    page = signed_in
    navigation = page.get_by_role("navigation", name="Family Controls")
    navigation.get_by_role("button", name="My account", exact=True).click()
    page.get_by_label("Display name", exact=True).fill("Alex Müller")
    page.get_by_role("button", name="Save profile", exact=True).click()
    expect(page.locator(".rail-footer strong")).to_have_text("Alex Müller")
    telegram = page.get_by_role("region", name="Telegram", exact=True)
    expect(telegram).to_contain_text("Your account is not linked")
    telegram.get_by_role("button", name="Connect my account").click()
    command = telegram.get_by_label("One-use linking command").input_value()
    token = command.split()[1]
    assert (
        household.api.post(
            "/api/v1/adapter/link",
            headers=adapter,
            json={"token": token, "externalId": "7", "label": "@alex"},
        ).status_code
        == 200
    )
    expect(telegram.get_by_role("button", name="Confirm this account")).to_be_visible(timeout=12000)
    telegram.get_by_role("button", name="Confirm this account").click()
    expect(telegram.get_by_role("button", name="Disconnect account")).to_be_visible()
    capture(page, "personal-account-desktop.png")
    navigation.get_by_role("button", name="Household administration", exact=True).click()
    page.get_by_role("navigation", name="Household settings").get_by_role(
        "button", name="Integrations", exact=True
    ).click()
    expect(page.get_by_role("region", name="Telegram", exact=True)).to_contain_text("Alex Müller")
    capture(page, "integrations-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    capture(page, "integrations-phone.png")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    navigation.get_by_role("button", name="My account", exact=True).click()
    telegram.get_by_role("button", name="Disconnect account").click()
    telegram.locator(".alert.warning").get_by_role("button", name="Disconnect account").click()
    expect(telegram).to_contain_text("Your account is not linked")
    expect(page.get_by_role("region", name="Matrix", exact=True)).to_contain_text("Not configured")
    capture(page, "personal-account-phone.png")
    page.locator(".rail-footer select").select_option("de")
    expect(page.locator(".settings-form select")).to_have_value("de")
    page.get_by_role("button", name="Profil speichern", exact=True).click()
    expect(page.get_by_role("heading", name="Mein Konto", exact=True)).to_be_visible()
    assert household.api.get("/api/v1/me").json()["language"] == "de"


def test_household_and_chat_languages_and_personal_inheritance(signed_in, household):
    household.config.localAdapters["family-controls-telegram"] = "telegram"
    page = signed_in
    household.api.put('/api/v1/me/profile', json={'language': 'en'})
    page.get_by_role('navigation', name='Family Controls').get_by_role('button', name='Household administration', exact=True).click()
    page.get_by_role('navigation', name='Household settings').get_by_role('button', name='Integrations', exact=True).click()
    page.get_by_label('Household language', exact=True).select_option('de')
    page.get_by_role('button', name='Save language', exact=True).first.click()
    expect(page.get_by_role('status')).to_contain_text('Language saved')
    telegram = page.get_by_role('region', name='Telegram', exact=True)
    telegram.get_by_label('Group or room language').select_option('en')
    telegram.get_by_role('button', name='Save language').click()
    expect(page.get_by_role('status')).to_contain_text('Language saved')
    assert household.api.get('/api/v1/preferences').json() == {'defaultLanguage': 'de', 'chatLanguages': {'telegram': 'en'}}
    capture(page, 'chat-languages-desktop.png')
    page.set_viewport_size({'width': 390, 'height': 844})
    capture(page, 'chat-languages-phone.png')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('navigation', name='Family Controls').get_by_role('button', name='My account', exact=True).click()
    page.locator('#main').get_by_label('Language', exact=True).select_option('')
    page.get_by_role('button', name='Save profile', exact=True).click()
    expect(page.get_by_role('heading', name='Mein Konto', exact=True)).to_be_visible()
    assert household.api.get('/api/v1/me').json()['language'] is None


def test_child_display_name_is_shared_by_policy_status_and_dashboard(signed_in, household):
    page = signed_in
    page.get_by_role('navigation').get_by_role('button', name='Schedules', exact=True).click()
    page.get_by_label('Account', exact=True).select_option('mia')
    page.get_by_label('Display name', exact=True).fill('Mia Sophie')
    page.get_by_role('button', name='Save routine', exact=True).click()
    expect(page.get_by_role('status')).to_contain_text('Saved')
    assert household.api.get('/api/v1/children/mia/status').json()['displayName'] == 'Mia Sophie'
    page.get_by_role('navigation').get_by_role('button', name='Today', exact=True).click()
    expect(page.get_by_role('heading', name='Mia Sophie', exact=True)).to_be_visible()
