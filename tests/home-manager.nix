{ self, pkgs }:
let
  inherit (pkgs) lib;
  # A small host interface tests our exported module wiring without introducing
  # another production flake input. Actual Home Manager activation is separate.
  evaluate =
    enabled:
    (lib.evalModules {
      specialArgs = {
        inherit pkgs;
        osConfig.services.desktopManager.plasma6.enable = enabled;
      };
      modules = [
        self.homeManagerModules.default
        self.homeManagerModules.plasma
        {
          options = {
            home.packages = lib.mkOption {
              type = lib.types.listOf lib.types.package;
              default = [ ];
            };
            systemd.user.services = lib.mkOption {
              type = lib.types.attrsOf lib.types.anything;
              default = { };
            };
            xdg.desktopEntries = lib.mkOption {
              type = lib.types.attrsOf lib.types.anything;
              default = { };
            };
            xdg.configFile = lib.mkOption {
              type = lib.types.attrsOf lib.types.anything;
              default = { };
            };
            programs.plasma.window-rules = lib.mkOption {
              type = lib.types.listOf lib.types.anything;
              default = [ ];
            };
          };
        }
        {
          services.family-controls.desktop.enable = enabled;
          services.family-controls.timekprClient = {
            enable = enabled;
            autostart = false;
            notifications.enable = false;
          };
        }
      ];
    }).config;
  enabled = evaluate true;
  disabled = evaluate false;
in
assert lib.assertMsg (
  enabled.systemd.user.services.family-controls-desktop.Service.ExecStart == "${
    self.packages.${pkgs.stdenv.hostPlatform.system}.family-controls-desktop
  }/bin/family-controls-desktop"
) "Countdown package injection must resolve through the exported module";
assert lib.assertMsg (
  disabled.home.packages == [ ]
  && disabled.systemd.user.services == { }
  && disabled.xdg.configFile == { }
) "Disabled Home Manager features must emit no services or files";
assert lib.assertMsg (
  !(enabled.xdg.configFile ? "autostart/timekpr-client.desktop")
) "Timekpr autostart=false must be respected";
assert lib.assertMsg (lib.hasInfix "SHOW_ALL_NOTIFICATIONS = False"
  enabled.xdg.configFile."timekpr/timekpr.conf".text
) "Notification settings must reach the generated configuration";
assert lib.assertMsg (
  (builtins.head enabled.programs.plasma.window-rules).match.window-class.value
  == "family-controls-countdown"
) "Plasma must match the real Tk window class";
pkgs.writeText "family-controls-home-manager-wiring" (builtins.toJSON enabled.systemd.user.services)
