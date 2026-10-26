{ config, self, ... }: {
  flake.homeManagerModules.countdown =
    {
      config,
      lib,
      familyControlsPackages,
      ...
    }:
    let
      cfg = config.services.family-controls.desktop;
    in
    {
      options.services.family-controls.desktop = {
        enable = lib.mkEnableOption "family controls countdown helper";
      };

      config = lib.mkIf cfg.enable {
        home.packages = [ familyControlsPackages."family-controls-desktop" ];

        xdg.desktopEntries.family-controls = {
          name = "Family Controls";
          comment = "See your screen time and request more";
          exec = "${familyControlsPackages.family-controls-desktop}/bin/family-controls-desktop --show";
          icon = "preferences-system-time";
          terminal = false;
          categories = [ "Utility" ];
        };
        systemd.user.services.family-controls-desktop = {
          Unit = {
            Description = "Family controls countdown helper";
            PartOf = [ "graphical-session.target" ];
            After = [ "graphical-session.target" ];
            StartLimitIntervalSec = 60;
            StartLimitBurst = 5;
          };

          Service = {
            ExecStart = "${familyControlsPackages."family-controls-desktop"}/bin/family-controls-desktop";
            Restart = "on-failure";
            RestartSec = 10;
          };

          Install = {
            WantedBy = [ "graphical-session.target" ];
          };
        };
      };
    };
  flake.homeManagerModules.timekpr = # Timekpr Client
    # Provides the timekpr-next client application and managed client config
    # for users subject to parental controls.
    #
    # Prerequisites: System must have parental controls enabled via
    # services.family-controls.agent.enable = true
    {
      config,
      lib,
      pkgs,
      ...
    }:

    let
      cfg = config.services.family-controls.timekprClient;
      notificationLevels = if cfg.notifications.enable then "3600[3];1800[2];600[1];300[0]" else "";
      playTimeNotificationLevels = if cfg.notifications.enable then "180[1]" else "";
    in
    {
      options.services.family-controls.timekprClient = {
        enable = lib.mkEnableOption "timekpr-next client tray icon for parental controls";

        autostart = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = "Whether to autostart the timekpr-next tray client on login.";
        };

        notifications.enable = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = ''
            Whether to allow child-facing Timekpr client notifications. When false,
            the managed client config disables standard, PlayTime, limit-change,
            sound, and speech notifications.
          '';
        };
      };

      config = lib.mkIf cfg.enable {
        home.packages = [ pkgs.timekpr ];

        xdg.configFile = {
          "timekpr/timekpr.conf".text = ''
            [CONFIG]
            # Managed by Home Manager.
            LOG_LEVEL = 1
            SHOW_LIMIT_NOTIFICATION = ${if cfg.notifications.enable then "True" else "False"}
            SHOW_ALL_NOTIFICATIONS = ${if cfg.notifications.enable then "True" else "False"}
            USE_SPEECH_NOTIFICATIONS = False
            SHOW_SECONDS = True
            NOTIFICATION_TIMEOUT = 3
            NOTIFICATION_TIMEOUT_CRITICAL = 10
            USE_NOTIFICATION_SOUNDS = False
            NOTIFICATION_LEVELS = ${notificationLevels}
            PLAYTIME_NOTIFICATION_LEVELS = ${playTimeNotificationLevels}
          '';
        }
        // lib.optionalAttrs cfg.autostart {
          "autostart/timekpr-client.desktop".text = ''
            [Desktop Entry]
            Type=Application
            Name=Timekpr Client
            Comment=Screen time tracker tray icon
            Exec=${pkgs.timekpr}/bin/timekprc
            Icon=timekpr-client
            Terminal=false
            Categories=System;Monitor;
            X-GNOME-Autostart-enabled=true
            X-KDE-autostart-after=panel
          '';
        };
      };
    };
  flake.homeManagerModules.plasma = # Import with plasma-manager when composing a Plasma desktop.
    {
      lib,
      osConfig ? null,
      ...
    }:
    {
      config =
        lib.optionalAttrs (osConfig != null && (osConfig.services.desktopManager.plasma6.enable or false))
          {
            programs.plasma.window-rules = [
              {
                description = "Family controls countdown stays above fullscreen apps";
                match = {
                  window-class = {
                    value = "family-controls-countdown";
                    type = "substring";
                  };
                };
                apply = {
                  above = {
                    value = true;
                    apply = "force";
                  };
                };
              }
            ];
          };
    };
  flake.homeManagerModules.default = { pkgs, ... }: {
    imports = [
      config.flake.homeManagerModules.countdown
      config.flake.homeManagerModules.timekpr
      config.flake.homeManagerModules.plasma
    ];
    _module.args.familyControlsPackages = self.packages.${pkgs.stdenv.hostPlatform.system};
  };
}
