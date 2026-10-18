{ self }:
{
  name = "family-controls";
  node.pkgsReadOnly = false;
  globalTimeout = 300;
  nodes.machine = { pkgs, lib, ... }: {
    imports = [ self.nixosModules.default ];
    system.stateVersion = "25.11";
    time.timeZone = "Europe/Berlin";
    services.timesyncd.enable = false;
    users.users.child.isNormalUser = true;
    users.users.sibling.isNormalUser = true;
    environment.systemPackages = [
      pkgs.curl
      (pkgs.python3.withPackages (ps: [
        ps.pydantic
        ps.pytest
      ]))
    ];
    environment.etc."family-test-token" = {
      text = "test-only-token";
      mode = "0600";
    };
    custom.system.features.parentalControls = {
      enable = true;
      timekprCountdownSeconds = 2;
      agent = {
        apiTokenFile = "/etc/family-test-token";
        bindHost = "0.0.0.0";
        openFirewall = true;
      };
      managedUsers = builtins.listToAttrs (
        map
          (name: {
            inherit name;
            value = {
              blockTTYLogin = false;
              killSessions = false;
              countLockedTime = name == "child";
              playTime = {
                dailyLimitMinutes = 60;
                activities = [ ".*family-test-game.*" ];
              };
              timeWindows.allowed.ALL = [ "00:00-24:00" ];
              dnsProfiles = {
                restrictedProfile = "restricted";
                homeworkProfile = "homework";
                leisureProfile = "leisure";
              };
            };
          })
          [
            "child"
            "sibling"
          ]
      );
    };
    systemd.services = builtins.listToAttrs (
      map
        (name: {
          name = "family-test-session-${name}";
          value = {
            wantedBy = [ "multi-user.target" ];
            # Finish each PAM login before starting the next synthetic session.
            after = lib.optional (name == "sibling") "family-test-session-child.service";
            environment = {
              XDG_SESSION_TYPE = "x11";
              XDG_SESSION_CLASS = "user";
            };
            serviceConfig = {
              Type = "exec";
              User = name;
              PAMName = "login";
              ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
            };
          };
        })
        [
          "child"
          "sibling"
        ]
    );
    virtualisation.memorySize = 1536;
  };
  nodes.relay = { pkgs, ... }: {
    system.stateVersion = "25.11";
    users.groups.family-controls-relay = { };
    users.users.family-controls-relay = {
      isSystemUser = true;
      group = "family-controls-relay";
    };
    environment.systemPackages = [
      pkgs.curl
      (pkgs.python3.withPackages (ps: [
        ps.pydantic
        ps.pytest
      ]))
    ];
    # Synthetic credentials exist only inside the isolated test network.
    environment.etc."family-test-token" = {
      text = "test-only-token";
      mode = "0440";
      group = "family-controls-relay";
    };
    systemd.tmpfiles.rules = [
      "d /var/lib/family-controls-relay 0700 family-controls-relay family-controls-relay -"
    ];
    virtualisation.memorySize = 768;
  };
  testScript = ''
    import json
    import shlex

    start_all()
    machine.wait_for_unit("timekprd.service", timeout=60)
    machine.wait_for_unit("family-controlsd.service", timeout=60)
    machine.wait_for_unit("family-test-session-child.service", timeout=60)
    machine.wait_for_unit("family-test-session-sibling.service", timeout=60)
    machine.wait_for_open_port(18451, timeout=60)
    relay.wait_for_unit("multi-user.target")
    certificate = machine.succeed("cat /var/lib/family-controls/tls/server.crt")
    relay.succeed("printf %s " + shlex.quote(certificate) + " > /etc/family-test-ca.crt")
    relay_tests = "runuser -u family-controls-relay -- env PYTHONPATH=${../pkgs/family-controls/src} python3 -m pytest -q -p no:cacheprovider -W error ${./relay_vm.py}"
    base = "family-controlsctl --host localhost:18451 --token-file /etc/family-test-token --ca-file /var/lib/family-controls/tls/server.crt"
    def command(action, operation=None):
        suffix = (" --operation-id " + shlex.quote(operation)) if operation else ""
        return json.loads(machine.succeed(base + suffix + " " + action))

    with subtest("real Timekpr D-Bus policy application and verified TLS"):
        machine.wait_until_succeeds(base + " remote-status child | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"application\"][\"status\"] == \"applied\"'", timeout=60)
        result = command("add-time child 25", "grant-once")
        assert result["effectiveDailyLimitMinutes"] == 85, result
        assert result["application"]["status"] == "applied", result
        assert command("add-time child 25", "grant-once")["duplicate"]
        machine.fail("curl --fail --silent https://localhost:18451/v1/users/child/status")

    with subtest("unprivileged relay on a separate VM through verified HTTPS to Timekpr"):
        relay.fail("curl --fail --silent --connect-timeout 5 https://machine:18451/v1/users/child/status")
        code = relay.succeed("curl --silent --connect-timeout 5 --cacert /etc/family-test-ca.crt --output /dev/null --write-out '%{http_code}' https://machine:18451/v1/users/child/status")
        assert code == "401", code
        relay.succeed(relay_tests + " -k button")

    with subtest("network outage and relay process restart retain one durable grant"):
        command("restore-today child")
        machine.succeed("iptables -I INPUT -p tcp --dport 18451 -j REJECT")
        try:
            relay.succeed(relay_tests + " -k offline")
            # Reboot also proves the inbox is durable beyond a Python process.
            relay.shutdown()
            relay.start()
            relay.wait_for_unit("multi-user.target")
        finally:
            machine.succeed("iptables -D INPUT -p tcp --dport 18451 -j REJECT")
        relay.succeed(relay_tests + " -k recovery")
        assert command("remote-status child")["effectiveDailyLimitMinutes"] == 67

    with subtest("real Unix peer credentials isolate siblings"):
        request = json.loads(machine.succeed("runuser -u sibling -- family-controlsctl request-time --minutes 15"))
        assert request["user"] == "sibling" and request["pendingRequest"]
        assert command("remote-status child")["pendingRequest"] is None
        machine.fail("family-controlsctl status")
        machine.fail("runuser -u child -- rm /run/family-controls/control.sock")

    with subtest("daily overrides, manual block, and restoration"):
        assert command("unlimited-today child")["unlimitedToday"]
        result = command("block child 1")
        assert result["blocked"] and result["application"]["status"] == "applied", result
        command("unblock child")
        result = command("restore-today child")
        assert result["effectiveDailyLimitMinutes"] == 60 and not result["unlimitedToday"], result

    with subtest("durable operation survives agent restart"):
        command("add-time child 10", "restart-grant")
        machine.succeed("systemctl restart family-controlsd")
        machine.wait_for_unit("family-controlsd")
        machine.wait_for_open_port(18451, timeout=30)
        result = command("add-time child 10", "restart-grant")
        assert result["effectiveDailyLimitMinutes"] == 70 and result["duplicate"], result

    with subtest("real locked-session accounting and native activity enforcement"):
        machine.succeed("systemctl restart family-test-session-child")
        machine.wait_until_succeeds("loginctl show-user child --property=Sessions --value | xargs -n1 loginctl show-session --property=Type --value | grep -qx x11", timeout=20)
        sessions = machine.succeed("loginctl show-user child --property=Sessions --value").split()
        session = next(s for s in sessions if machine.succeed(f"loginctl show-session {s} --property=Type --value").strip() == "x11")
        path = machine.succeed(f"busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager GetSession s {session}").split('"')[1]
        machine.succeed(f"busctl call org.freedesktop.login1 {path} org.freedesktop.login1.Session SetLockedHint b true")
        machine.wait_until_succeeds(base + " remote-status child | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"countedLockedSeconds\"] >= 2'", timeout=30)
        machine.succeed(f"busctl call org.freedesktop.login1 {path} org.freedesktop.login1.Session SetLockedHint b false")
        command("set-limit child 60")
        machine.succeed("systemd-run --unit family-test-game --property=RemainAfterExit=yes --uid child /run/current-system/sw/bin/python3 -c 'import time; time.sleep(600)' family-test-game")
        machine.wait_for_unit("family-test-game.service")
        machine.wait_until_succeeds("timekpra --userinfo child | grep -q 'ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT: 1'", timeout=30)
        command("set-limit child 0")
        machine.wait_until_succeeds("test $(systemctl show family-test-game --property=MainPID --value) = 0", timeout=40)
        exit_status = machine.succeed("systemctl show family-test-game --property=ExecMainStatus --value").strip()
        assert exit_status in {"15", "9"}, exit_status
        machine.succeed("systemctl stop family-test-game")
        machine.succeed("systemctl is-active --quiet family-test-session-child.service")
        command("restore-today child")

    with subtest("SIGKILL restart retains durable intent"):
        command("add-time child 10", "crash-grant")
        machine.succeed("systemctl kill --kill-whom=main --signal=SIGKILL family-controlsd")
        machine.wait_until_succeeds(base + " remote-status child", timeout=30)
        result = command("add-time child 10", "crash-grant")
        assert result["duplicate"] and result["effectiveDailyLimitMinutes"] == 70, result

    with subtest("midnight expires temporary policy and pending requests"):
        command("allow-today child")
        machine.succeed("date -s 'tomorrow 12:00:00'")
        machine.wait_until_succeeds(base + " remote-status child | python3 -c 'import json,sys; s=json.load(sys.stdin); assert not s[\"unlimitedToday\"] and s[\"effectiveDailyLimitMinutes\"] == 60 and s[\"application\"][\"status\"] == \"applied\"'", timeout=30)
        assert command("remote-status sibling")["request"]["status"] == "expired"

    with subtest("service stop restores recurring policy"):
        command("allow-today child")
        machine.succeed("systemctl stop family-controlsd")
        info = machine.succeed("timekpra --userinfo child")
        assert "PLAYTIME_LIMITS_PER_WEEKDAYS: 3600;3600;3600;3600;3600;3600;3600" in info, info
        machine.succeed("test -s /var/lib/family-controls/state.sqlite")
  '';
}
