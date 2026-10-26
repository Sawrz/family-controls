{ self }:
let
  policy = name: {
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
  names = [
    "child"
    "sibling"
  ];
in
{
  name = "family-controls-central";
  node.pkgsReadOnly = false;
  globalTimeout = 420;
  nodes.machine = { pkgs, lib, ... }: {
    imports = [ self.nixosModules.default ];
    system.stateVersion = "25.11";
    time.timeZone = "Europe/Berlin";
    services.timesyncd.enable = false;
    users.users.child.isNormalUser = true;
    users.users.sibling.isNormalUser = true;
    environment.systemPackages = [
      pkgs.curl
      pkgs.python3
    ];
    services.family-controls.agent = {
      enable = true;
      deviceId = "machine";
      backend = {
        url = "https://backend:18450";
        caFile = "/etc/backend-ca.crt";
      };
      enrollmentTokenFile = "/etc/enrollment-token";
      timekprCountdownSeconds = 2;
      managedUsers = builtins.listToAttrs (
        map (name: {
          inherit name;
          value = policy name;
        }) names
      );
    };
    # The test driver installs the runtime CA/token before starting this service.
    systemd.services =
      builtins.listToAttrs (
        map (name: {
          name = "family-test-session-${name}";
          value = {
            wantedBy = [ "multi-user.target" ];
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
        }) names
      )
      // {
        family-controls-agent.wantedBy = lib.mkForce [ ];
      };
    virtualisation.memorySize = 1536;
  };
  nodes.backend = { pkgs, ... }: {
    imports = [ self.nixosModules.default ];
    system.stateVersion = "25.11";
    time.timeZone = "Europe/Berlin";
    services.timesyncd.enable = false;
    networking.firewall.allowedTCPPorts = [ 18450 ];
    environment.systemPackages = [
      pkgs.curl
      pkgs.family-controlsctl
      (pkgs.python3.withPackages (ps: [
        ps.pydantic
        ps.httpx
        ps.pytest
      ]))
    ];
    # Synthetic test credentials only; production uses runtime secret providers.
    environment.etc."admin-password" = {
      text = "isolated-test-password";
      mode = "0440";
      group = "family-controls-backend";
    };
    environment.etc."interface-token" = {
      text = "isolated-interface-token";
      mode = "0444";
    };
    services.family-controls.backend = {
      enable = true;
      settings = {
        publicUrl = "https://backend:18450";
        bindHost = "0.0.0.0";
        tlsCertFile = "/var/lib/family-controls-backend/server.crt";
        tlsKeyFile = "/var/lib/family-controls-backend/server.key";
        authentication.bootstrapPasswordFile = "/etc/admin-password";
        children = builtins.listToAttrs (
          map (name: {
            inherit name;
            value = {
              device = "machine";
              user = name;
              timezone = "Europe/Berlin";
              policy = policy name;
            };
          }) names
        );
        interfaces = {
          telegram = {
            kind = "telegram";
            tokenFile = "/etc/interface-token";
          };
          matrix = {
            kind = "matrix";
            tokenFile = "/etc/matrix-interface-token";
          };
        };
      };
    };
    environment.etc."matrix-interface-token".text = "isolated-matrix-token";
    systemd.services.family-controls-backend.preStart = ''
      if [ ! -s /var/lib/family-controls-backend/server.key ]; then
        ${pkgs.openssl}/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
          -subj /CN=backend -addext subjectAltName=DNS:backend,DNS:localhost,DNS:web-upstream \
          -keyout /var/lib/family-controls-backend/server.key \
          -out /var/lib/family-controls-backend/server.crt
      fi
    '';
    virtualisation.memorySize = 1024;
  };
  testScript = ''
    import json
    import shlex
    import uuid

    start_all()
    backend.wait_for_unit("family-controls-backend")
    backend.wait_for_open_port(18450)
    machine.wait_for_unit("timekprd")
    machine.wait_for_unit("family-test-session-child")
    machine.wait_for_unit("family-test-session-sibling")
    cert = backend.succeed("cat /var/lib/family-controls-backend/server.crt")
    machine.succeed("printf %s " + shlex.quote(cert) + " > /etc/backend-ca.crt")
    base = "family-controlsctl --backend https://backend:18450 --ca-file /var/lib/family-controls-backend/server.crt"
    backend.succeed(base + " login --username admin --password-file /etc/admin-password --output-token-file /tmp/admin-token")
    base += " --token-file /tmp/admin-token"
    def api(route, body=None, method=None):
        args = " api " + shlex.quote(route)
        if body is not None:
            backend.succeed("printf %s " + shlex.quote(json.dumps(body)) + " > /tmp/body.json")
            args += " --body-file /tmp/body.json"
        args += " --method " + (method or ("POST" if body is not None else "GET"))
        return json.loads(backend.succeed(base + args))

    def status(child="child"):
        return api("/api/v1/children/" + child + "/status")

    def command(action, body=None, operation=None):
        operation = operation or str(uuid.uuid4())
        backend.succeed("printf %s " + shlex.quote(json.dumps(body or {})) + " > /tmp/body.json")
        output = backend.succeed(base + " --operation-id " + shlex.quote(operation) + " api /api/v1/children/child/commands/" + action + " --method POST --body-file /tmp/body.json")
        return json.loads(output)

    def wait_budget(minutes):
        backend.wait_until_succeeds(base + " remote-status child | python3 -c " + shlex.quote("import json,sys; s=json.load(sys.stdin); assert s['effectiveDailyLimitMinutes']==" + str(minutes) + " and s['delivery']['pending']==0 and s['application']['status']=='applied', s"), timeout=60)

    with subtest("verified TLS, outbound enrollment and explicit approval"):
        backend.fail("curl --fail --silent https://backend:18450/health")
        invitation = api("/api/v1/devices/machine/invitation", {})["token"]
        machine.succeed("printf %s " + shlex.quote(invitation) + " > /etc/enrollment-token")
        machine.succeed("chmod 600 /etc/enrollment-token; systemctl start family-controls-agent")
        backend.wait_until_succeeds(base + " api /api/v1/devices | python3 -c \"import json,sys; assert json.load(sys.stdin)['devices'][0]['status']=='pending'\"", timeout=30)
        api("/api/v1/devices/machine/approve", {})
        wait_budget(60)
        machine.fail("ss -ltn | grep -q ':18451 '")

    with subtest("durable command delivery and replay"):
        command("add-time", {"minutes": 25}, "once")
        wait_budget(85)
        assert command("add-time", {"minutes": 25}, "once")["duplicate"]
        wait_budget(85)
        command("restore-today")
        wait_budget(60)

    with subtest("Telegram and Matrix pass through the real central API"):
        api("/api/v1/identities", {"interface": "telegram", "externalId": "501", "account": "admin"})
        api("/api/v1/identities", {"interface": "matrix", "externalId": "@parent:test", "account": "admin"})
        backend.succeed("env PYTHONPATH=${../pkgs/family-controls/src} python3 -m pytest -q -p no:cacheprovider ${./relay_vm.py}")
        wait_budget(72)
        command("restore-today")
        wait_budget(60)

    with subtest("network outage and backend reboot retain the queued grant"):
        machine.succeed("iptables -I OUTPUT -p tcp --dport 18450 -j REJECT")
        command("add-time", {"minutes": 7}, "outage")
        assert status()["delivery"]["pending"] == 1
        backend.shutdown()
        backend.start()
        backend.wait_for_unit("family-controls-backend")
        backend.wait_for_open_port(18450)
        machine.succeed("iptables -D OUTPUT -p tcp --dport 18450 -j REJECT")
        wait_budget(67)
        assert command("add-time", {"minutes": 7}, "outage")["duplicate"]
        command("restore-today")
        wait_budget(60)

    with subtest("local sibling identity and request approval"):
        request = json.loads(machine.succeed("runuser -u sibling -- family-controlsctl request-time --minutes 15"))
        assert request["user"] == "sibling" and request["pendingRequest"]
        machine.fail("family-controlsctl status")
        machine.fail("runuser -u child -- rm /run/family-controls/control.sock")
        backend.wait_until_succeeds(base + " remote-status sibling | python3 -c \"import json,sys; assert json.load(sys.stdin)['pendingRequest']\"", timeout=30)
        assert status()["pendingRequest"] is None

    with subtest("real locked-session accounting and activity enforcement"):
        sessions = machine.succeed("loginctl show-user child --property=Sessions --value").split()
        session = next(s for s in sessions if machine.succeed(f"loginctl show-session {s} --property=Type --value").strip() == "x11")
        path = machine.succeed(f"busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager GetSession s {session}").split('"')[1]
        machine.succeed(f"busctl call org.freedesktop.login1 {path} org.freedesktop.login1.Session SetLockedHint b true")
        backend.wait_until_succeeds(base + " remote-status child | python3 -c \"import json,sys; assert json.load(sys.stdin)['countedLockedSeconds']>=2\"", timeout=30)
        machine.succeed(f"busctl call org.freedesktop.login1 {path} org.freedesktop.login1.Session SetLockedHint b false")
        machine.succeed("systemd-run --unit family-test-game --property=RemainAfterExit=yes --uid child /run/current-system/sw/bin/python3 -c 'import time; time.sleep(600)' family-test-game")
        machine.wait_for_unit("family-test-game")
        machine.wait_until_succeeds("timekpra --userinfo child | grep -q 'ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT: 1'", timeout=30)
        command("set-limit", {"minutes": 0})
        machine.wait_until_succeeds("test $(systemctl show family-test-game --property=MainPID --value) = 0", timeout=40)
        assert machine.succeed("systemctl show family-test-game --property=ExecMainStatus --value").strip() in {"15", "9"}
        command("restore-today")
        wait_budget(60)

    with subtest("agent crash preserves operation receipts and cached policy"):
        command("add-time", {"minutes": 10}, "crash")
        wait_budget(70)
        machine.succeed("systemctl kill --kill-whom=main --signal=SIGKILL family-controls-agent")
        machine.wait_for_unit("family-controls-agent")
        assert command("add-time", {"minutes": 10}, "crash")["duplicate"]
        wait_budget(70)

    with subtest("midnight and stop restoration work without central connectivity"):
        command("allow-today")
        backend.wait_until_succeeds(base + " remote-status child | python3 -c \"import json,sys; assert json.load(sys.stdin)['unlimitedToday']\"", timeout=30)
        machine.succeed("iptables -I OUTPUT -p tcp --dport 18450 -j REJECT")
        machine.succeed("date -s 'tomorrow 12:00:00'")
        machine.wait_until_succeeds("runuser -u child -- family-controlsctl status | python3 -c \"import json,sys; s=json.load(sys.stdin); assert not s['unlimitedToday'] and s['effectiveDailyLimitMinutes']==60\"", timeout=30)
        machine.succeed("systemctl stop family-controls-agent")
        info = machine.succeed("timekpra --userinfo child")
        assert "PLAYTIME_LIMITS_PER_WEEKDAYS: 3600;3600;3600;3600;3600;3600;3600" in info
  '';
}
