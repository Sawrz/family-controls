# Timekpr Client
# Provides the timekpr-next client application and managed client config
# for users subject to parental controls.
#
# Prerequisites: System must have parental controls enabled via
# custom.system.features.parentalControls.enable = true
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.custom.configs.timekprClient;
  notificationLevels = if cfg.notifications.enable then "3600[3];1800[2];600[1];300[0]" else "";
  playTimeNotificationLevels = if cfg.notifications.enable then "180[1]" else "";
in
{
  options.custom.configs.timekprClient = {
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
}
