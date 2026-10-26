{ self }:
{ lib, ... }:
{
  imports = [ (import ./nixos.nix { inherit self; }) ];
  name = lib.mkForce "family-controls-web-journeys";
  globalTimeout = lib.mkForce 1200;
  extraPythonPackages = ps: [
    ps.pytest
    ps.playwright
  ];

  nodes.backend = { pkgs, ... }: {
    services.family-controls.backend.settings.children.child.assignments = lib.mkForce [
      {
        device = "machine";
        user = "child";
      }
      {
        device = "laptop";
        user = "child";
      }
    ];
    services.family-controls.backend.settings.publicUrl = lib.mkForce "https://backend";
    services.family-controls.interfaces.web = {
      enable = true;
      hostName = "backend";
      backendUrl = "https://web-upstream:18450";
      backendCAFile = "/run/family-test-tls/server.crt";
      sslCertificate = "/run/family-test-tls/server.crt";
      sslCertificateKey = "/run/family-test-tls/server.key";
    };
    systemd.services.nginx = {
      after = [ "family-test-tls.service" ];
      requires = [ "family-test-tls.service" ];
    };
    systemd.services.family-test-tls = {
      after = [ "family-controls-backend.service" ];
      wants = [ "family-controls-backend.service" ];
      serviceConfig.Type = "oneshot";
      serviceConfig.RemainAfterExit = true;
      script = ''
        install -d -m 0750 -g nginx /run/family-test-tls
        install -m 0640 -g nginx /var/lib/family-controls-backend/server.crt /run/family-test-tls/
        install -m 0640 -g nginx /var/lib/family-controls-backend/server.key /run/family-test-tls/
      '';
    };
    networking.firewall.allowedTCPPorts = [
      443
      9223
    ];
    networking.hosts."127.0.0.1" = [ "web-upstream" ];
    fonts.packages = [ pkgs.dejavu_fonts ];
    users.users.parent-browser = {
      isNormalUser = true;
    };
    systemd.services.family-test-browser = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        User = "parent-browser";
        ExecStart = "${pkgs.chromium}/bin/chromium --headless --disable-gpu --no-first-run --no-default-browser-check --remote-debugging-port=9222 --user-data-dir=/home/parent-browser/chromium about:blank";
      };
    };
    # Test-only CDP bridge: the browser itself remains inside the backend VM.
    systemd.services.family-test-browser-bridge = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.socat}/bin/socat TCP-LISTEN:9223,reuseaddr,fork TCP:127.0.0.1:9222";
    };
    virtualisation.memorySize = lib.mkForce 2048;
    virtualisation.cores = 2;
  };

  nodes.laptop = { ... }: {
    imports = [ (import ./nixos.nix { inherit self; }).nodes.machine ];
    services.family-controls.agent.deviceId = lib.mkForce "laptop";
    services.family-controls.agent.managedUsers = lib.mkForce {
      child = {
        blockTTYLogin = false;
        killSessions = false;
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
    };
  };

  nodes.machine = { pkgs, ... }: {
    fonts.packages = [ pkgs.dejavu_fonts ];
    environment.systemPackages = [
      pkgs.xdotool
      pkgs.dunst
      pkgs.scrot
      pkgs.family-controls-desktop
    ];
    systemd.services.family-test-display = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.xorg-server}/bin/Xvfb :99 -screen 0 1024x768x24 -nolisten tcp";
    };
    systemd.services.family-test-desktop = {
      after = [
        "family-test-display.service"
        "family-test-session-child.service"
      ];
      requires = [ "family-test-display.service" ];
      environment.DISPLAY = ":99";
      path = [ pkgs.dbus ];
      serviceConfig = {
        User = "child";
        ExecStart = "${pkgs.dbus}/bin/dbus-run-session ${pkgs.writeShellScript "family-test-desktop" ''
          set -eu
          printf '%s' "$DBUS_SESSION_BUS_ADDRESS" > /home/child/notification-bus
          ${pkgs.openbox}/bin/openbox &
          ${pkgs.dunst}/bin/dunst &
          exec ${pkgs.family-controls-desktop}/bin/family-controls-desktop
        ''}";
      };
    };
  };

  testScript = lib.mkForce ''
    import os
    from pathlib import Path
    from types import SimpleNamespace
    import pytest

    start_all()
    backend.wait_for_unit("nginx")
    backend.wait_for_open_port(18450)
    backend.wait_for_open_port(443)
    backend.wait_for_open_port(9222)
    backend.forward_port(9222, 9223)
    backend.forward_port(8443, 443)
    machine.wait_for_unit("timekprd")
    machine.wait_for_unit("family-test-session-child")
    machine.wait_for_unit("family-test-session-sibling")
    laptop.wait_for_unit("timekprd")
    laptop.wait_for_unit("family-test-session-child")

    class VMFixtures:
        @pytest.fixture(scope="session")
        def vm_nodes(self):
            return SimpleNamespace(machine=machine, laptop=laptop, backend=backend)

    output = Path(driver.out_dir)
    os.environ["VM_ARTIFACTS"] = str(output)
    try:
        result = pytest.main([
            "${./web_vm/test_journeys.py}", "-c", "${./web_vm/pytest.ini}",
            "-o", "cache_dir=" + str(output / ".pytest_cache"),
            "-v", "-s", "-x", "--junitxml=" + str(output / "web-journeys.xml"),
        ], plugins=[VMFixtures()])
        assert result == 0, "Browser-to-device journeys failed; see pytest and VM logs"
    finally:
        machine.succeed("DISPLAY=:99 scrot /tmp/child-final.png")
        machine.copy_from_machine("/tmp/child-final.png", str(output))
        for node in (machine, laptop, backend):
            (output / (node.name + "-journal.txt")).write_text(node.succeed("journalctl -b --no-pager"))
  '';
}
