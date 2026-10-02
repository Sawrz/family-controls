{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    listToAttrs
    map
    mapAttrs
    mapAttrsToList
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    mkAfter
    optionalAttrs
    types
    ;

  cfg = config.custom.system.features.parentalControls;
  jsonFormat = pkgs.formats.json { };

  managedUserModule = types.submodule {
    options = {
      timeWindows = {
        allowed = mkOption {
          type = types.attrsOf (types.listOf types.str);
          default = { };
        };
        unmetered = mkOption {
          type = types.attrsOf (types.listOf types.str);
          default = { };
        };
      };

      playTime = {
        dailyLimitMinutes = mkOption {
          type = types.nullOr types.int;
          default = null;
        };
        activities = mkOption {
          type = types.listOf types.str;
          default = [ ];
        };
      };

      trackInactive = mkOption {
        type = types.bool;
        default = false;
      };

      countLockedTime = mkOption {
        type = types.bool;
        default = false;
      };

      blockTTYLogin = mkOption {
        type = types.bool;
        default = true;
      };

      lockoutAction = mkOption {
        type = types.enum [
          "terminate"
          "suspend"
          "shutdown"
          "lock"
          "kill"
        ];
        default = "terminate";
      };

      killSessions = mkOption {
        type = types.bool;
        default = true;
      };

      dnsProfiles = {
        restrictedProfile = mkOption {
          type = types.str;
        };
        homeworkProfile = mkOption {
          type = types.str;
        };
        leisureProfile = mkOption {
          type = types.str;
        };
      };

      shell = mkOption {
        type = types.package;
        default = pkgs.zsh;
      };
    };
  };

  timekprManagedUsersJson = jsonFormat.generate "family-timekpr-users.json" {
    managedUsers = mapAttrs (_name: userCfg: {
      inherit (userCfg) trackInactive;
      lockoutAction =
        if userCfg.killSessions && userCfg.lockoutAction == "terminate" then
          "kill"
        else
          userCfg.lockoutAction;
      timeWindows = {
        inherit (userCfg.timeWindows) allowed unmetered;
      };
      playTime = {
        inherit (userCfg.playTime) dailyLimitMinutes activities;
      };
    }) cfg.managedUsers;
  };

  timekprPreStart = pkgs.writeShellScript "timekpr-family-controls-prestart" ''
    set -euo pipefail
    export FAMILY_TIMEKPR_CONFIG=${timekprManagedUsersJson}

    mkdir -p /var/lib/timekpr/config
    mkdir -p /var/lib/timekpr/work

    ${pkgs.python3}/bin/python3 <<'PY'
    import json
    import os
    from pathlib import Path

    def parse_hhmm(value):
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    def parse_range(value, unmetered=False):
        start, end = value.split("-", 1)
        start_m = parse_hhmm(start)
        end_m = parse_hhmm(end)
        if end_m <= start_m:
            raise ValueError(f"invalid range: {value}")
        return (start_m, end_m, unmetered)

    def windows_for_day(window_map, day):
        return window_map.get(day, window_map.get("ALL", []))

    def build_minutes(allowed, unmetered):
        minutes = [0] * 1440
        for start, end, _ in allowed:
            for minute in range(start, end):
                minutes[minute] = 1
        for start, end, _ in unmetered:
            for minute in range(start, end):
                minutes[minute] = 2
        return minutes

    def token(hour, start_minute, end_minute, unmetered=False):
        if start_minute == 0 and end_minute == 60:
            value = str(hour)
        else:
            value = f"{hour}[{start_minute:02d}-{end_minute:02d}]"
        return f"!{value}" if unmetered else value

    def timekpr_hours(allowed_ranges, unmetered_ranges):
        minutes = build_minutes(allowed_ranges, unmetered_ranges)
        out = []
        cursor = 0
        while cursor < 1440:
            state = minutes[cursor]
            if state == 0:
                cursor += 1
                continue
            end = cursor
            while end < 1440 and minutes[end] == state:
                end += 1
            subcursor = cursor
            while subcursor < end:
                hour = subcursor // 60
                hour_end = min(end, (hour + 1) * 60)
                minute_end = hour_end % 60 if hour_end % 60 != 0 else 60
                out.append(token(hour, subcursor % 60, minute_end, unmetered=state == 2))
                subcursor = hour_end
            cursor = end
        return ";".join(out)

    cfg = json.loads(Path(os.environ["FAMILY_TIMEKPR_CONFIG"]).read_text())
    for user, user_cfg in cfg["managedUsers"].items():
        daily_limit_seconds = int(user_cfg["playTime"]["dailyLimitMinutes"]) * 60
        activities = user_cfg["playTime"]["activities"]
        activity_lines = "\n".join(
            f"PLAYTIME_ACTIVITY_{idx:03d} = {activity}"
            for idx, activity in enumerate(activities, start=1)
        )
        content = [
            "[DOCUMENTATION]",
            "#### generated by family controls",
            f"[{user}]",
        ]
        for day in range(1, 8):
            allowed = [parse_range(value) for value in windows_for_day(user_cfg["timeWindows"]["allowed"], str(day))]
            unmetered = [
                parse_range(value, unmetered=True)
                for value in windows_for_day(user_cfg["timeWindows"]["unmetered"], str(day))
            ]
            content.append(f"ALLOWED_HOURS_{day} = {timekpr_hours(allowed, unmetered)}")
        content.extend([
            "ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7",
            "LIMITS_PER_WEEKDAYS = " + ";".join(["86400"] * 7),
            f"TRACK_INACTIVE = {'True' if user_cfg['trackInactive'] else 'False'}",
            "HIDE_TRAY_ICON = False",
            f"LOCKOUT_TYPE = {user_cfg['lockoutAction']}",
            "WAKEUP_HOUR_INTERVAL = 0;23",
            "",
            f"[{user}.PLAYTIME]",
            "PLAYTIME_ENABLED = True",
            "PLAYTIME_LIMIT_OVERRIDE_ENABLED = False",
            "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED = False",
            "PLAYTIME_ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7",
            "PLAYTIME_LIMITS_PER_WEEKDAYS = " + ";".join([str(daily_limit_seconds)] * 7),
            "##PLAYTIME_ACTIVITIES## Do NOT remove or alter this line!",
            activity_lines,
            "",
        ])
        Path(f"/var/lib/timekpr/config/timekpr.{user}.conf").write_text("\n".join(content), encoding="utf-8")
    PY
  '';

  ttyWrapper =
    username: userCfg:
    pkgs.writeShellScriptBin "tty-blocker-shell-${username}" ''
      tty_path="$(tty 2>/dev/null || true)"
      case "$tty_path" in
        /dev/tty[0-9]*)
          echo "TTY login is disabled for this account."
          sleep 2
          exit 1
          ;;
      esac

      exec ${userCfg.shell}/bin/${
        userCfg.shell.meta.mainProgram or (lib.getName userCfg.shell)
      } --login "$@"
    '';

  agentConfig = jsonFormat.generate "family-controls-agent.json" {
    inherit (cfg) warningLeadSeconds pollIntervalSeconds;
    timezone = config.time.timeZone;
    managedUsers = mapAttrs (_name: userCfg: {
      inherit (userCfg)
        trackInactive
        countLockedTime
        blockTTYLogin
        lockoutAction
        killSessions
        ;
      timeWindows = {
        inherit (userCfg.timeWindows) allowed unmetered;
      };
      playTime = {
        inherit (userCfg.playTime) dailyLimitMinutes activities;
      };
      dnsProfiles = {
        inherit (userCfg.dnsProfiles) restrictedProfile homeworkProfile leisureProfile;
      };
    }) cfg.managedUsers;
    agent = {
      inherit (cfg.agent) port stateDir;
      socketPath = "/run/family-controls/control.sock";
      socketGroup = "family-controls";
      tlsCertPath = "${cfg.agent.stateDir}/tls/server.crt";
      tlsKeyPath = "${cfg.agent.stateDir}/tls/server.key";
    };
    system = {
      loginctlBinary = "${config.systemd.package}/bin/loginctl";
    };
    timekpr = {
      adminBinary = "${pkgs.timekpr}/bin/timekpra";
    };
  };

  tlsBootstrap = pkgs.writeShellScript "family-controls-tls-bootstrap" ''
    set -euo pipefail
    install -d -m 0750 "${cfg.agent.stateDir}/tls"
    if [ ! -f "${cfg.agent.stateDir}/tls/server.key" ] || [ ! -f "${cfg.agent.stateDir}/tls/server.crt" ]; then
      ${pkgs.openssl}/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -subj "/CN=${config.networking.hostName}" \
        -keyout "${cfg.agent.stateDir}/tls/server.key" \
        -out "${cfg.agent.stateDir}/tls/server.crt"
      chmod 0600 "${cfg.agent.stateDir}/tls/server.key"
      chmod 0644 "${cfg.agent.stateDir}/tls/server.crt"
    fi
  '';
in
{
  options.custom.system.features.parentalControls = {
    enable = mkEnableOption "family controls child-host feature";

    adminUsers = mkOption {
      type = types.listOf types.str;
      default = [ ];
    };

    managedUsers = mkOption {
      type = types.attrsOf managedUserModule;
      default = { };
    };

    warningLeadSeconds = mkOption {
      type = types.int;
      default = 300;
    };

    timekprCountdownSeconds = mkOption {
      type = types.int;
      default = 60;
    };

    pollIntervalSeconds = mkOption {
      type = types.int;
      default = 1;
    };

    mutableUsers = mkOption {
      type = types.bool;
      default = true;
    };

    kiosk = {
      enable = mkOption {
        type = types.bool;
        default = false;
      };
      profile = mkOption {
        type = types.enum [ "strict" ];
        default = "strict";
      };
    };

    firefox.lockDnsOverHttps = mkOption {
      type = types.bool;
      default = true;
    };

    agent = {
      enable = mkOption {
        type = types.bool;
        default = true;
      };
      port = mkOption {
        type = types.port;
        default = 18451;
      };
      stateDir = mkOption {
        type = types.str;
        default = "/var/lib/family-controls";
      };
      apiTokenSecret = mkOption {
        type = types.nullOr types.str;
        default = null;
      };
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.managedUsers != { };
        message = "parentalControls requires at least one managed user.";
      }
      {
        assertion = builtins.all (
          userCfg: userCfg.playTime.dailyLimitMinutes != null && userCfg.playTime.activities != [ ]
        ) (builtins.attrValues cfg.managedUsers);
        message = "Each managed user must set playTime.dailyLimitMinutes and at least one playTime.activities entry.";
      }
      {
        assertion = !cfg.agent.enable || cfg.agent.apiTokenSecret != null;
        message = "parentalControls.agent.apiTokenSecret is required when the family controls agent is enabled.";
      }
    ];

    environment.systemPackages = [
      pkgs.timekpr
      pkgs."family-controlsd"
      pkgs."family-controlsctl"
    ];

    services.dbus.packages = [ pkgs.timekpr ];

    boot.loader.systemd-boot.editor = false;

    users = {
      groups = {
        timekpr = { };
        family-controls = { };
      };

      inherit (cfg) mutableUsers;

      users =
        let
          adminAttrs = listToAttrs (
            map (user: lib.nameValuePair user { extraGroups = mkAfter [ "timekpr" ]; }) cfg.adminUsers
          );
          managedAttrs = listToAttrs (
            mapAttrsToList (
              username: userCfg:
              lib.nameValuePair username (
                {
                  extraGroups = mkAfter [ "family-controls" ];
                }
                // optionalAttrs userCfg.blockTTYLogin {
                  shell = lib.mkForce "${ttyWrapper username userCfg}/bin/tty-blocker-shell-${username}";
                }
              )
            ) cfg.managedUsers
          );
        in
        mkMerge [
          adminAttrs
          managedAttrs
        ];
    };

    sops.secrets = optionalAttrs (cfg.agent.enable && cfg.agent.apiTokenSecret != null) {
      ${cfg.agent.apiTokenSecret} = { };
    };

    networking.firewall.allowedTCPPorts = lib.optional cfg.agent.enable cfg.agent.port;

    systemd = {
      tmpfiles.rules = [
        "d /var/lib/timekpr 0755 root root -"
        "d /var/lib/timekpr/config 0755 root root -"
        "d /var/lib/timekpr/work 0755 root root -"
        "d ${cfg.agent.stateDir} 0750 root root -"
        "d /run/family-controls 0770 root family-controls -"
      ];

      services = {
        timekprd = {
          description = "timekpr-nExT - screen time manager daemon";
          after = [ "dbus.service" ];
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "simple";
            ExecStartPre = timekprPreStart;
            ExecStart = "${pkgs.timekpr}/bin/timekprd";
            Restart = "on-failure";
            RestartSec = "5s";
          };
        };

        family-controlsd = mkIf cfg.agent.enable {
          description = "Family controls child-host agent";
          after = [
            "network-online.target"
            "timekprd.service"
          ];
          wants = [ "network-online.target" ];
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "simple";
            ExecStartPre = tlsBootstrap;
            ExecStart = "${pkgs."family-controlsd"}/bin/family-controlsd --config ${agentConfig} --token-file ${
              config.sops.secrets.${cfg.agent.apiTokenSecret}.path
            }";
            Restart = "always";
            RestartSec = "5s";
          };
        };
      };
    };

    environment.etc = mkMerge [
      {
        "timekpr/timekpr.conf" = {
          text = ''
            [DOCUMENTATION]
            #### generated by family controls

            [GENERAL]
            TIMEKPR_LOGLEVEL = 2
            TIMEKPR_POLLTIME = ${toString cfg.pollIntervalSeconds}
            TIMEKPR_SAVE_TIME = 30
            TIMEKPR_TRACK_INACTIVE = False
            TIMEKPR_TERMINATION_TIME = ${toString cfg.timekprCountdownSeconds}
            TIMEKPR_FINAL_WARNING_TIME = 0
            TIMEKPR_FINAL_NOTIFICATION_TIME = 0

            [SESSION]
            TIMEKPR_SESSION_TYPES_CTRL = x11;wayland;mir
            TIMEKPR_SESSION_TYPES_EXCL = tty;unspecified
            TIMEKPR_USERS_EXCL = testtimekpr;gdm;gdm3;kdm;lightdm;mdm;lxdm;xdm;sddm;cdm

            [DIRECTORIES]
            TIMEKPR_CONFIG_DIR = /var/lib/timekpr/config
            TIMEKPR_WORK_DIR = /var/lib/timekpr/work
            TIMEKPR_SHARED_DIR = ${pkgs.timekpr}/share/timekpr
            TIMEKPR_LOGFILE_DIR = /var/log

            [PLAYTIME]
            TIMEKPR_PLAYTIME_ENABLED = True
            TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED = True
          '';
          mode = "0644";
        };
      }
      (mkIf cfg.kiosk.enable {
        "xdg/kdeglobals".text = ''
          [KDE Action Restrictions][$i]
          custom_config=false
          run_command=false
          action/run_command=false
          run_desktop_files=true
          shell_access=false
          action/start_new_session=false
          action/switch_user=false
          ghns=false
          plasma/plasmashell/unlockedDesktop=false
          plasma-desktop/scripting_console=false
          plasma-desktop/add_activities=false

          [KDE Resource Restrictions][$i]
          autostart=false
        '';

        "xdg/KDE/UserFeedback.conf".text = ''
          [UserFeedback]
          Enabled=false
        '';
      })
    ];

    programs.firefox.policies = mkIf (config.programs.firefox.enable && cfg.firefox.lockDnsOverHttps) {
      DNSOverHTTPS = {
        Enabled = false;
        Locked = true;
        Fallback = false;
      };
    };
  };
}
