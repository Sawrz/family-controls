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
          type = types.nullOr (types.ints.between 0 1440);
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
        description = "Charge measured locked intervals while all graphical sessions are locked. Mutually exclusive with trackInactive.";
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

  timekprPreStart = pkgs.writeShellScript "timekpr-family-controls-prestart" ''
    set -euo pipefail
    mkdir -p /var/lib/timekpr/config /var/lib/timekpr/work
    ${pkgs."family-controls-render"}/bin/family-controls-render --config ${agentConfig}
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

  agentConfig = pkgs.runCommand "validated-family-controls-agent.json" { } ''
    ${pkgs.family-controlsd}/bin/family-controlsd --config ${rawAgentConfig} --check-config
    cp ${rawAgentConfig} "$out"
  '';

  rawAgentConfig = jsonFormat.generate "family-controls-agent.json" {
    inherit (cfg)
      warningLeadSeconds
      pollIntervalSeconds
      reconciliationSeconds
      auditRetentionDays
      ;
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
      inherit (cfg.agent) port stateDir bindHost;
      socketPath = "/run/family-controls/control.sock";
      socketGroup = "family-controls";
      tlsCertPath = cfg.agent.tlsCertPath;
      tlsKeyPath = cfg.agent.tlsKeyPath;
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
    if [ "${cfg.agent.tlsKeyPath}" != "${cfg.agent.stateDir}/tls/server.key" ] || [ "${cfg.agent.tlsCertPath}" != "${cfg.agent.stateDir}/tls/server.crt" ]; then
      test -s "${cfg.agent.tlsKeyPath}"
      test -s "${cfg.agent.tlsCertPath}"
      exit 0
    fi
    if [ ! -f "${cfg.agent.stateDir}/tls/server.key" ] && [ ! -f "${cfg.agent.stateDir}/tls/server.crt" ]; then
      ${pkgs.openssl}/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -subj "/CN=${config.networking.hostName}" \
        -addext "subjectAltName=DNS:${config.networking.hostName},DNS:localhost,IP:127.0.0.1" \
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
      type = types.ints.between 0 86400;
      default = 300;
    };

    timekprCountdownSeconds = mkOption {
      type = types.ints.positive;
      default = 60;
    };

    pollIntervalSeconds = mkOption {
      type = types.ints.positive;
      default = 2;
    };

    reconciliationSeconds = mkOption {
      type = types.ints.positive;
      default = 30;
    };
    auditRetentionDays = mkOption {
      type = types.ints.between 1 3650;
      default = 365;
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
      bindHost = mkOption {
        type = types.str;
        default = "127.0.0.1";
        description = "Listener address; remote exposure is selected in the deployment.";
      };
      openFirewall = mkOption {
        type = types.bool;
        default = false;
      };
      tlsCertPath = mkOption {
        type = types.str;
        default = "${cfg.agent.stateDir}/tls/server.crt";
        description = "Server certificate with a matching subjectAltName.";
      };
      tlsKeyPath = mkOption {
        type = types.str;
        default = "${cfg.agent.stateDir}/tls/server.key";
      };
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
      apiTokenFile = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Runtime credential file, as an alternative to a SOPS secret name.";
      };
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = config.time.timeZone != null;
        message = "parentalControls requires an explicit time.timeZone so schedules and daily expiry share one timezone.";
      }
      {
        assertion = builtins.all (user: !(user.countLockedTime && user.trackInactive)) (
          builtins.attrValues cfg.managedUsers
        );
        message = "Choose countLockedTime or trackInactive; overlapping accounting is not supported.";
      }
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
        assertion = !cfg.agent.enable || cfg.agent.apiTokenSecret != null || cfg.agent.apiTokenFile != null;
        message = "parentalControls.agent requires apiTokenSecret or apiTokenFile.";
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

    networking.firewall.allowedTCPPorts = lib.optional (
      cfg.agent.enable && cfg.agent.openFirewall
    ) cfg.agent.port;

    systemd = {
      tmpfiles.rules = [
        "d /var/lib/timekpr 0755 root root -"
        "d /var/lib/timekpr/config 0755 root root -"
        "d /var/lib/timekpr/work 0755 root root -"
        "d ${cfg.agent.stateDir} 0750 root root -"
        "d /run/family-controls 0750 root family-controls -"
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
          wants = [
            "network-online.target"
            "timekprd.service"
          ];
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "simple";
            ExecStartPre = tlsBootstrap;
            ExecStart = "${pkgs."family-controlsd"}/bin/family-controlsd --config ${agentConfig} --token-file ${
              if cfg.agent.apiTokenFile != null then
                cfg.agent.apiTokenFile
              else
                config.sops.secrets.${cfg.agent.apiTokenSecret}.path
            }";
            ExecStopPost = "${pkgs."family-controlsd"}/bin/family-controlsd --config ${agentConfig} --restore";
            NoNewPrivileges = true;
            ProtectSystem = "strict";
            ProtectHome = true;
            PrivateTmp = true;
            ReadWritePaths = [
              cfg.agent.stateDir
              "/run/family-controls"
            ];
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            TimeoutStopSec = 90;
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
