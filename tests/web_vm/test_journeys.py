"""Parent browser journeys through deployed NixOS services and real device enforcement.

The Nix test driver supplies VM control fixtures. No application ports or
transports are replaced. Browser writes always use the parent interface.
"""

import json
import os
import shlex
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


class Household:
    """Coordinate browser actions with observations on the child's VM."""

    def __init__(self, nodes, page):
        self.nodes = nodes
        self.page = page

    def local(self, user="child"):
        return json.loads(
            self.nodes.machine.succeed(
                f"runuser -u {shlex.quote(user)} -- family-controlsctl status"
            )
        )

    def reported(self, user="child"):
        return self.read(f"/api/v1/children/{user}/status")

    def read(self, path):
        return self.page.evaluate(
            """async path => {
            const response = await fetch(path, {signal: AbortSignal.timeout(15000)});
            if (!response.ok) throw new Error(`Read failed: ${response.status}`);
            return response.json();
        }""",
            path,
        )

    def wait(self, predicate, *, timeout=45):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            latest = self.reported()
            if predicate(latest):
                return latest
            time.sleep(0.5)
        pytest.fail(f"Device report did not reach expected state: {latest}")

    def budget(self, minutes):
        result = self.wait(
            lambda s: (
                s["effectiveDailyLimitMinutes"] == minutes
                and s["delivery"]["pending"] == 0
                and s["application"]["status"] == "applied"
            )
        )
        assert self.local()["effectiveDailyLimitMinutes"] == minutes
        return result

    def navigate(self, label):
        self.page.get_by_role("navigation").get_by_role("button", name=label, exact=True).click()

    def row(self, user="child"):
        return self.page.locator(".child-row").filter(
            has=self.page.get_by_role("heading", name=user, exact=True)
        )

    def change(self, action, minutes=None):
        self.navigate("Today")
        close = self.page.get_by_role("button", name="Close", exact=True)
        if close.count():
            close.click()
        with self.page.expect_response("**/children/child/status"):
            self.page.get_by_role("button", name="Refresh", exact=True).click()
        self.row().get_by_role("button", name="Change time").click()
        self.page.get_by_label("Change", exact=True).select_option(action)
        if minutes is not None:
            self.page.get_by_label("Custom duration").fill(str(minutes))
        confirmation = self.page.locator(".change-preview input[type=checkbox]")
        if confirmation.count():
            confirmation.check()
        self.page.get_by_role("button", name="Apply change", exact=True).click()

    def ui(self, command):
        address = self.nodes.machine.succeed("cat /home/child/notification-bus").strip()
        return self.nodes.machine.succeed(
            "runuser -u child -- env DISPLAY=:99 DBUS_SESSION_BUS_ADDRESS="
            + shlex.quote(address)
            + " sh -c "
            + shlex.quote(command),
            timeout=30,
        )

    def start_game(self):
        self.nodes.machine.succeed("systemctl stop family-test-game.service || true")
        self.nodes.machine.succeed(
            "systemd-run --unit family-test-game --property=RemainAfterExit=yes --uid child "
            "/run/current-system/sw/bin/python3 -c 'import time; time.sleep(600)' family-test-game"
        )
        self.nodes.machine.wait_until_succeeds(
            "timekpra --userinfo child | grep -q 'ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT: 1'",
            timeout=30,
        )

    def screenshot(self, name):
        self.page.screenshot(path=str(Path(os.environ["VM_ARTIFACTS"]) / name), full_page=True)


@pytest.fixture(scope="session")
def household(vm_nodes):
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1440, "height": 1000}
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        expect.set_options(timeout=30000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("https://backend")
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("isolated-test-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="Today’s family plan")).to_be_visible()
        home = Household(vm_nodes, page)
        try:
            yield home
        finally:
            home.screenshot("parent-final.png")
            context.close()
            browser.close()
            assert not errors, errors


def test_enrollment_requires_parent_approval_in_browser(household):
    home, page = household, household.page
    expect(home.row().get_by_text("Unknown", exact=True)).to_be_visible()
    home.navigate("Settings")
    form = page.locator("form").filter(
        has=page.get_by_role("heading", name="Create device enrollment token")
    )
    form.get_by_role("button", name="Create device enrollment token").click()
    token = page.get_by_label("Copy this token now").input_value()
    assert token
    cert = home.nodes.backend.succeed("cat /var/lib/family-controls-backend/server.crt")
    home.nodes.machine.succeed("printf %s " + shlex.quote(cert) + " > /etc/backend-ca.crt")
    home.nodes.machine.succeed("printf %s " + shlex.quote(token) + " > /etc/enrollment-token")
    home.nodes.machine.succeed(
        "chmod 600 /etc/enrollment-token; systemctl start family-controls-agent"
    )
    device = page.locator(".record-list li").filter(has=page.get_by_text("machine", exact=True))
    expect(device.get_by_role("button", name="Approve")).to_be_visible(timeout=45000)
    assert home.reported()["connectivity"] != "online"
    device.get_by_role("button", name="Approve").click()
    home.budget(60)
    assert home.local("sibling")["effectiveDailyLimitMinutes"] == 60


def test_custom_extension_survives_lost_response_without_double_grant(household):
    home, page = household, household.page

    def lose_response(route):
        # Forward the original UI request to the guest, then discard its response.
        response = route.fetch(
            url=route.request.url.replace("https://backend", "https://127.0.0.1:8443"),
            timeout=15000,
        )
        assert response.ok, response.text()
        route.abort("connectionreset")

    page.route("**/commands/add-time", lose_response, times=1)
    home.change("add-time", 27)
    expect(page.get_by_role("button", name="Retry the same change")).to_be_visible()
    home.budget(87)
    page.reload()
    page.get_by_role("button", name="Retry the same change").click()
    home.budget(87)
    info = home.nodes.machine.succeed("timekpra --userinfo child")
    limits = next(
        line.split(": ", 1)[1]
        for line in info.splitlines()
        if line.startswith("PLAYTIME_LIMITS_PER_WEEKDAYS:")
    )
    today = int(home.nodes.machine.succeed("date +%u").strip()) - 1
    expected = ["3600"] * 7
    expected[today] = "5220"
    assert limits.split(";") == expected
    assert home.local("sibling")["effectiveDailyLimitMinutes"] == 60
    history = home.read("/api/v1/children/child/history")["events"]
    assert len([e for e in history if e["action"] == "add-time"]) == 1
    home.change("restore-today")
    home.budget(60)


def test_native_warnings_request_and_parent_decision(household):
    home, page = household, household.page
    home.nodes.machine.succeed("systemctl start family-test-desktop")
    home.nodes.machine.wait_until_succeeds(
        "test -S /run/user/1000/family-controls-ui.sock", timeout=30
    )
    home.change("set-remaining", 5)
    home.wait(lambda s: 0 < s["remainingCountdownSeconds"] <= 300 and s["delivery"]["pending"] == 0)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        home.ui("dunstctl close-all")
        if "5 minutes remaining" in home.ui("dunstctl history"):
            break
        time.sleep(1)
    assert "5 minutes remaining" in home.ui("dunstctl history")
    # Close the automatic warning window; the notification action must reopen it.
    home.ui(
        "xdotool search --onlyvisible --name '^Family Controls$' windowactivate --sync; xdotool key alt+F4"
    )
    home.change("set-remaining", 1)
    home.wait(lambda s: 0 < s["remainingCountdownSeconds"] <= 60 and s["delivery"]["pending"] == 0)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and home.ui("dunstctl count displayed").strip() == "0":
        time.sleep(0.5)
    home.ui(
        "xdotool search --onlyvisible --name '^Family Controls$' windowactivate --sync; xdotool key alt+F4"
    )
    home.ui("dunstctl action")
    home.ui("xdotool search --sync --onlyvisible --name '^Family Controls$' windowactivate --sync")
    home.ui(
        "xdotool key Tab Home shift+End; xdotool type '17m'; xdotool key Tab; "
        "xdotool type 'Finish my VM project'; xdotool key Tab space"
    )
    home.wait(lambda s: s.get("pendingRequest") is not None)
    assert home.local()["pendingRequest"]["minutes"] == 17
    home.navigate("Today")
    page.locator(".request-banner").get_by_role("button", name="Review request").click()
    expect(page.get_by_label("Custom duration")).to_have_value("17")
    page.get_by_label("Reason (optional)").fill("Finish the project")
    page.get_by_role("button", name="Send decision").click()
    home.wait(lambda s: s.get("pendingRequest") is None and s["delivery"]["pending"] == 0)
    assert home.local()["request"]["status"] == "approved"
    assert home.local()["remainingPlayTimeSeconds"] > 16 * 60
    home.ui("dunstctl close-all")
    assert "1 minute remaining" in home.ui("dunstctl history")
    home.ui("scrot /home/child/request-approved.png")
    home.nodes.machine.copy_from_machine(
        "/home/child/request-approved.png", os.environ["VM_ARTIFACTS"]
    )
    home.screenshot("request-approved-parent.png")
    home.change("restore-today")
    home.budget(60)


def test_exhaustion_extension_unlimited_and_block_reach_real_enforcement(household):
    home = household
    home.start_game()
    home.change("set-remaining", 1)
    home.wait(lambda s: 0 < s["remainingPlayTimeSeconds"] <= 60 and s["delivery"]["pending"] == 0)
    stopped = "test $(systemctl show family-test-game --property=MainPID --value) = 0"
    home.nodes.machine.wait_until_succeeds(stopped, timeout=100)
    assert home.nodes.machine.succeed(
        "systemctl show family-test-game --property=ExecMainStatus --value"
    ).strip() in {"15", "9"}
    previous = home.local()["effectiveDailyLimitMinutes"]
    home.change("add-time", 10)
    home.budget(previous + 10)
    home.start_game()
    home.change("unlimited-today")
    home.wait(lambda s: s.get("unlimitedToday") and s["delivery"]["pending"] == 0)
    assert home.local()["unlimitedToday"]
    home.nodes.machine.fail(stopped)
    home.change("block")
    home.wait(lambda s: s.get("blocked") and s["delivery"]["pending"] == 0)
    home.nodes.machine.wait_until_succeeds(stopped, timeout=45)
    assert home.local("sibling")["blocked"] is False
    home.change("unblock")
    home.wait(lambda s: not s.get("blocked") and s["delivery"]["pending"] == 0)
    home.change("restore-today")
    home.budget(60)


def test_offline_grant_survives_backend_restart_and_applies_once(household):
    home, page = household, household.page
    home.nodes.machine.block()
    try:
        home.wait(lambda s: s["connectivity"] == "offline", timeout=60)
        home.change("add-time", 7)
        expect(home.row().get_by_text("Unknown", exact=True)).to_be_visible()
        home.wait(lambda s: s["delivery"]["pending"] == 1)
        assert home.local()["effectiveDailyLimitMinutes"] == 60
        home.nodes.backend.succeed("systemctl restart family-controls-backend")
        home.nodes.backend.wait_for_unit("family-controls-backend")
        home.nodes.backend.wait_for_open_port(18450)
        page.reload()
        expect(page.get_by_role("heading", name="Today’s family plan")).to_be_visible()
        assert home.reported()["delivery"]["pending"] == 1
    finally:
        home.nodes.machine.unblock()
    home.budget(67)
    history = home.read("/api/v1/children/child/history")["events"]
    grants = [e for e in history if e["action"] == "add-time"]
    assert len(grants) == 3
    assert all(e["outcome"] == "applied" for e in grants)
    assert home.local("sibling")["effectiveDailyLimitMinutes"] == 60
    home.screenshot("offline-grant-recovered.png")
    home.change("restore-today")
    home.budget(60)


def test_saved_routine_reaches_device_and_survives_agent_restart(household):
    home, page = household, household.page
    home.navigate("Schedules")
    page.get_by_label("Account", exact=True).select_option("child")
    page.get_by_label("Daily allowance (minutes)").fill("75")
    page.get_by_role("button", name="Save routine", exact=True).click()
    home.budget(75)
    home.nodes.machine.succeed("systemctl restart family-controls-agent")
    home.nodes.machine.wait_for_unit("family-controls-agent")
    home.nodes.machine.wait_until_succeeds(
        "runuser -u child -- family-controlsctl status >/dev/null", timeout=30
    )
    home.budget(75)
    assert home.local("sibling")["effectiveDailyLimitMinutes"] == 60
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Language", exact=True).select_option("de")
    home.screenshot("german-routine-mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
