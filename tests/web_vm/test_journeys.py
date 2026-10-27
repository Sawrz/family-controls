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
        laptop = json.loads(
            self.nodes.laptop.succeed("runuser -u child -- family-controlsctl status")
        )
        assert laptop["effectiveDailyLimitMinutes"] == minutes
        return result

    def navigate(self, label):
        self.page.get_by_role("navigation", name="Family Controls").get_by_role(
            "button", name=label, exact=True
        ).click()

    def row(self, user="child"):
        return self.page.locator(".child-row").filter(
            has=self.page.get_by_role("heading", name=user[:1].upper() + user[1:], exact=True)
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
        # A manual block ends the logind session. Emulate the child signing in
        # again before launching an activity after access is restored.
        self.nodes.machine.succeed("systemctl start family-test-session-child")
        self.nodes.machine.succeed("systemctl stop family-test-game.service || true")
        self.nodes.machine.succeed(
            "systemd-run --unit family-test-game --property=RemainAfterExit=yes --uid child "
            "/run/current-system/sw/bin/python3 -c 'import time; time.sleep(600)' family-test-game"
        )
        try:
            self.nodes.machine.wait_until_succeeds(
                "timekpra --userinfo child | grep -q 'ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT: 1'",
                timeout=30,
            )
        except Exception:
            print(self.nodes.machine.succeed("timekpra --userinfo child"))
            print(self.nodes.machine.succeed("cat /var/lib/timekpr/config/timekpr.child.conf"))
            print(self.nodes.machine.succeed("systemctl status family-test-game --no-pager"))
            raise

    def screenshot(self, name):
        self.page.evaluate("window.scrollTo(0, 0)")
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


def test_declarative_enrollment_through_external_https_proxy(household):
    home, page = household, household.page
    expect(home.row().get_by_text("Unknown", exact=True)).to_be_visible()
    home.navigate("Household administration")
    page.get_by_role("navigation", name="Household settings").get_by_role(
        "button", name="Devices", exact=True
    ).click()
    expect(page.get_by_role("heading", name="Create device enrollment token")).to_have_count(0)
    cert = home.nodes.backend.succeed("cat /var/lib/family-controls-backend/server.crt")
    for name in ("machine", "laptop"):
        device = page.locator(".record-list li").filter(has=page.get_by_text(name, exact=True))
        expect(device.get_by_text("Waiting for first connection", exact=True)).to_be_visible()
        expect(device.get_by_role("button")).to_have_count(0)
        node = getattr(home.nodes, name)
        node.succeed("printf %s " + shlex.quote(cert) + " > /etc/backend-ca.crt")
        node.succeed("systemctl start family-controls-agent")
    home.budget(60)
    assert home.read("/api/v1/children/sibling/policy")["setupComplete"] is False
    home.nodes.machine.wait_until_succeeds(
        "runuser -u sibling -- family-controlsctl status | python3 -c "
        + shlex.quote(
            "import json,sys; assert json.load(sys.stdin)['effectiveDailyLimitMinutes'] == 0"
        )
    )
    home.navigate("Today")
    home.row("sibling").get_by_role("button", name="Set up limits", exact=True).click()
    setup = page.get_by_role("region", name="Set up limits Sibling")
    setup.get_by_role("button", name="Child", exact=True).click()
    home.screenshot("child-onboarding-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    home.screenshot("child-onboarding-phone.png")
    setup.get_by_role("button", name="Confirm limits", exact=True).click()
    expect(setup).to_have_count(0)
    home.nodes.machine.wait_until_succeeds(
        "runuser -u sibling -- family-controlsctl status | python3 -c "
        + shlex.quote(
            "import json,sys; s=json.load(sys.stdin); "
            "assert s['effectiveDailyLimitMinutes'] == 60; "
            "assert s['application']['status'] == 'applied'; "
            "assert s['remainingAllowedSeconds'] > 0; "
            "assert s['remainingPlayTimeSeconds'] > 0"
        )
    )
    assert home.read("/api/v1/children/sibling/policy")["setupComplete"] is True
    home.nodes.machine.succeed("systemctl start family-test-session-sibling")
    home.nodes.machine.wait_for_unit("family-test-session-sibling")
    page.set_viewport_size({"width": 1440, "height": 1000})
    home.navigate("Household administration")
    page.get_by_role("navigation", name="Household settings").get_by_role(
        "button", name="Devices", exact=True
    ).click()
    assert home.local("sibling")["effectiveDailyLimitMinutes"] == 60
    for name in ("machine", "laptop"):
        device = page.locator(".record-list li").filter(has=page.get_by_text(name, exact=True))
        expect(device.get_by_text("Approved", exact=True)).to_be_visible()
    home.screenshot("declarative-devices-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    home.screenshot("declarative-devices-phone.png")
    page.set_viewport_size({"width": 1440, "height": 1000})
    for device in home.read("/api/v1/devices")["devices"]:
        assert device["status"] == "approved" and device["managedBy"] == "configuration"
    home.nodes.backend.succeed(
        "curl --fail --silent -H 'Host: backend' http://127.0.0.1:18080/web-config.json"
    )


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
    expected = ["0"] * 7
    expected[today] = str(
        home.local()["localUsage"]["issuedSeconds"] - home.local()["localUsage"]["returnedSeconds"]
    )
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
    assert home.local()["remainingCountdownSeconds"] > 16 * 60
    assert home.local()["remainingPlayTimeSeconds"] <= 300
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


def test_two_computers_share_usage_and_weekly_report(household):
    home, page = household, household.page
    page.get_by_label("Sprache", exact=True).select_option("en")
    home.change("restore-today")
    home.budget(75)
    home.start_game()
    laptop = home.nodes.laptop
    laptop.succeed("systemctl start family-test-session-child")
    laptop.succeed(
        "systemd-run --unit family-test-game --property=RemainAfterExit=yes --uid child "
        "/run/current-system/sw/bin/python3 -c 'import time; time.sleep(600)' family-test-game"
    )
    laptop.wait_until_succeeds(
        "timekpra --userinfo child | grep -q 'ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT: 1'",
        timeout=30,
    )
    before = {
        d["device"]: d["status"]["localUsage"]["playSeconds"] for d in home.reported()["devices"]
    }
    # Native saved counters flush independently. Wait for both computers'
    # measured progress, not just a sum that one computer can satisfy alone.
    status = home.wait(
        lambda s: all(
            d["status"]["localUsage"]["playSeconds"] >= before[d["device"]] + 10
            for d in s["devices"]
        )
    )
    assert len(status["devices"]) == 2
    week = home.read("/api/v1/children/child/usage")
    today = next(d for d in week["days"] if len(d["devices"]) == 2)
    assert today["playSeconds"] == sum(d["playSeconds"] for d in today["devices"])
    assert all(d["playSeconds"] > 0 for d in today["devices"])
    assert status["reportedUsedSeconds"] + status["reservedSeconds"] <= 75 * 60
    home.navigate("Weekly usage")
    page.get_by_label("Account", exact=True).select_option("child")
    expect(page.locator(".usage-days")).to_contain_text("Metered play time")
    details = page.locator(".usage-days details").last
    details.get_by_text("Computer details", exact=True).click()
    expect(details).to_contain_text("laptop")
    expect(details).to_contain_text("machine")
    page.set_viewport_size({"width": 1440, "height": 1000})
    home.screenshot("shared-weekly-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Language", exact=True).select_option("de")
    home.screenshot("shared-weekly-mobile-de.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    home.nodes.machine.succeed("systemctl stop family-test-game")
    laptop.succeed("systemctl stop family-test-game")


def test_disconnected_computers_exhaust_only_their_reserved_credit(household):
    home, page = household, household.page
    page.get_by_label("Sprache", exact=True).select_option("en")
    # Both enforcers must observe the previous activities stopping before we
    # shrink the shared budget and wait for the redistributed idle allocations.
    home.wait(lambda s: all(not d["status"]["localUsage"]["needsTime"] for d in s["devices"]))
    home.change("set-remaining", 1)
    home.wait(
        lambda s: (
            s["delivery"]["pending"] == 0
            and s["reservedSeconds"] <= 60
            and all(d["status"]["remainingPlayTimeSeconds"] >= 10 for d in s["devices"])
        )
    )
    home.start_game()
    laptop = home.nodes.laptop
    laptop.succeed("systemctl start family-test-session-child")
    laptop.succeed(
        "systemd-run --unit family-test-game --property=RemainAfterExit=yes --uid child "
        "/run/current-system/sw/bin/python3 -c 'import time; time.sleep(600)' family-test-game"
    )
    status = home.reported()
    assert status["reservedSeconds"] <= 60
    before = status["reportedUsedSeconds"]
    home.nodes.machine.block()
    laptop.block()
    try:
        for node in (home.nodes.machine, laptop):
            node.wait_until_fails("pgrep -u child -f family-test-game", timeout=90)
    finally:
        home.nodes.machine.unblock()
        laptop.unblock()
    status = home.wait(
        lambda s: s["connectivity"] == "online" and s["reportedUsedSeconds"] > before
    )
    assert status["reportedUsedSeconds"] + status["reservedSeconds"] >= before
    home.nodes.machine.succeed("systemctl stop family-test-game")
    laptop.succeed("systemctl stop family-test-game")


def test_device_rotation_and_removal_preserve_reports(household):
    """Deploy changed trust, restart, and exercise real authenticated device requests."""
    import hashlib
    import re

    home = household
    backend, machine = home.nodes.backend, home.nodes.machine
    before = home.read("/api/v1/children/child/usage")
    unit = backend.succeed("systemctl cat family-controls-backend")
    command = shlex.split(re.search(r"^ExecStart=(.+)$", unit, re.MULTILINE).group(1))
    index = command.index("--config") + 1
    original = json.loads(backend.succeed("cat " + shlex.quote(command[index])))
    runtime = "/var/lib/family-controls-backend/test-config.json"
    command[index] = runtime
    override = "[Service]\nExecStart=\nExecStart=" + shlex.join(command) + "\n"
    backend.succeed("mkdir -p /run/systemd/system/family-controls-backend.service.d")
    backend.succeed(
        "printf %s "
        + shlex.quote(override)
        + " > /run/systemd/system/family-controls-backend.service.d/test.conf"
    )

    def deploy(config):
        backend.succeed("printf %s " + shlex.quote(json.dumps(config)) + " > " + runtime)
        backend.succeed("chown family-controls-backend:family-controls-backend " + runtime)
        backend.succeed("chmod 600 " + runtime)
        backend.succeed("systemctl daemon-reload")
        backend.succeed("systemctl restart family-controls-backend")
        backend.wait_for_open_port(18450)

    def request_status(token, device="machine"):
        return backend.succeed(
            "curl --silent --output /dev/null --write-out '%{http_code}' --cacert /var/lib/family-controls-backend/server.crt "
            "-H "
            + shlex.quote("Authorization: Bearer " + token)
            + " -H 'Content-Type: application/json' "
            "-d '{\"policyRevision\":0}' https://backend:18450/api/v1/devices/" + device + "/sync"
        ).strip()

    rotated = "vm-rotated-machine-token-0000000000000000"
    original["trustedDevices"]["machine"]["credentialHash"] = hashlib.sha256(
        rotated.encode()
    ).hexdigest()
    machine.succeed("systemctl stop family-controls-agent")
    deploy(original)
    assert request_status("vm-machine-declarative-token-000000000000") == "401"
    assert request_status(rotated, "laptop") == "401"
    machine.succeed("rm /etc/device-token")
    machine.succeed("umask 077; printf %s " + shlex.quote(rotated) + " > /etc/device-token")
    machine.succeed("systemctl start family-controls-agent")
    home.wait(lambda s: s["connectivity"] == "online")
    assert request_status(rotated) == "200"
    del original["trustedDevices"]["machine"]
    deploy(original)
    assert request_status(rotated) == "403"
    home.wait(lambda s: all(d["device"] != "machine" for d in s["devices"]))
    after = home.read("/api/v1/children/child/usage")
    assert sum(day["playSeconds"] or 0 for day in after["days"]) >= sum(
        day["playSeconds"] or 0 for day in before["days"]
    )
    assert (
        next(d for d in home.read("/api/v1/devices")["devices"] if d["device"] == "machine")[
            "status"
        ]
        == "revoked"
    )


def test_personal_connections_and_household_overview_use_os_trusted_adapter(household):
    home, page = household, household.page
    home.navigate("My account")
    page.get_by_label("Display name", exact=True).fill("Household owner")
    page.get_by_role("button", name="Save profile", exact=True).click()
    expect(page.locator(".rail-footer strong")).to_have_text("Household owner")
    telegram = page.get_by_role("region", name="Telegram", exact=True)
    telegram.get_by_role("button", name="Connect my account").click()
    token = telegram.get_by_label("One-use linking command").input_value().split()[1]

    def adapter(path, payload):
        # Only the external provider is simulated. Authenticate as the deployed
        # adapter OS user through the real Unix listener and central service.
        return home.nodes.backend.succeed(
            "runuser -u family-controls-telegram -- curl --fail --silent "
            "--unix-socket /run/family-controls-backend/adapters.sock "
            "-H 'Content-Type: application/json' --data "
            + shlex.quote(json.dumps(payload))
            + " http://localhost/api/v1/adapter/"
            + path
        )

    adapter("link", {"token": token, "externalId": "42", "label": "@parent_vm"})
    expect(telegram.get_by_role("button", name="Confirm this account")).to_be_visible()
    telegram.get_by_role("button", name="Confirm this account").click()
    expect(telegram.get_by_role("button", name="Disconnect account")).to_be_visible()
    adapter(
        "health", {"status": "connected", "bot": "@family_test", "destination": "VM parents (-42)"}
    )
    home.screenshot("personal-connections-desktop.png")
    home.navigate("Household administration")
    page.get_by_role("navigation", name="Household settings").get_by_role(
        "button", name="Integrations", exact=True
    ).click()
    expect(telegram).to_contain_text("Household owner")
    expect(telegram.get_by_text("Connected", exact=True)).to_be_visible()
    home.screenshot("household-integrations-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    home.screenshot("household-integrations-phone.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    home.navigate("My account")
    telegram.get_by_role("button", name="Disconnect account").click()
    telegram.locator(".alert.warning").get_by_role("button", name="Disconnect account").click()
    expect(telegram).to_contain_text("Your account is not linked")
    assert home.read("/api/v1/children/sibling/policy")["setupComplete"] is True
    page.set_viewport_size({"width": 1440, "height": 1000})
