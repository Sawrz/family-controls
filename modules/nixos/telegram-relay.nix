{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    filterAttrs
    listToAttrs
    mapAttrs
    mapAttrsToList
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;
  cfg = config.custom.services.family.telegramRelay;
  jsonFormat = pkgs.formats.json { };
  botTokenPath =
    if cfg.botTokenSecret == null then
      "/run/family-controls/missing-bot-token"
    else
      config.sops.secrets.${cfg.botTokenSecret}.path;
  relayConfig = pkgs.runCommand "validated-family-telegram-relay.json" { } ''
    ${pkgs.family-controls-relay}/bin/family-controls-relay --config ${rawRelayConfig} --check-config
    cp ${rawRelayConfig} "$out"
  '';

  rawRelayConfig = jsonFormat.generate "family-telegram-relay.json" {
    inherit (cfg) parentGroupChatId stateDir;
    hostChildren = mapAttrs (_name: child: {
      inherit (child)
        host
        port
        user
        deviceId
        caFile
        ;
      apiTokenFile =
        if child.apiTokenSecret == null then null else config.sops.secrets.${child.apiTokenSecret}.path;
    }) cfg.hostChildren;
  };
in
{
  options.custom.services.family.telegramRelay = {
    enable = mkEnableOption "family Telegram relay";

    stateDir = mkOption {
      type = types.str;
      default = "/var/lib/family-controls-relay";
      description = "Persistent operation inbox, conversations, and notification receipts.";
    };

    botTokenSecret = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "SOPS secret name containing the Telegram bot token.";
    };

    parentGroupChatId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Telegram group chat id for parent controls.";
    };

    hostChildren = mkOption {
      type = types.attrsOf (
        types.submodule {
          options = {
            host = mkOption {
              type = types.str;
            };
            port = mkOption {
              type = types.port;
              default = 18451;
            };
            user = mkOption {
              type = types.str;
            };
            deviceId = mkOption {
              type = types.str;
            };
            apiTokenSecret = mkOption {
              type = types.nullOr types.str;
              default = null;
            };
            caFile = mkOption {
              type = types.nullOr types.str;
              default = null;
              description = "Trusted CA certificate file for this child's HTTPS endpoint.";
            };
          };
        }
      );
      default = { };
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.botTokenSecret != null && cfg.parentGroupChatId != null;
        message = "family.telegramRelay requires botTokenSecret and parentGroupChatId when enabled.";
      }
      {
        assertion =
          cfg.hostChildren != { }
          && builtins.all (child: child.apiTokenSecret != null) (builtins.attrValues cfg.hostChildren);
        message = "family.telegramRelay requires children with API credentials.";
      }
    ];

    sops.secrets =
      optionalAttrs (cfg.botTokenSecret != null) {
        ${cfg.botTokenSecret}.owner = lib.mkDefault "family-controls-relay";
      }
      // listToAttrs (
        mapAttrsToList (
          _name: child:
          lib.nameValuePair child.apiTokenSecret { owner = lib.mkDefault "family-controls-relay"; }
        ) (filterAttrs (_name: child: child.apiTokenSecret != null) cfg.hostChildren)
      );

    users.groups.family-controls-relay = { };
    users.users.family-controls-relay = {
      isSystemUser = true;
      group = "family-controls-relay";
    };
    systemd.tmpfiles.rules = [ "d ${cfg.stateDir} 0700 family-controls-relay family-controls-relay -" ];

    systemd.services.family-controls-relay = {
      description = "Family controls Telegram relay";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "simple";
        User = "family-controls-relay";
        Group = "family-controls-relay";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ cfg.stateDir ];
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ];
        ExecStart = "${pkgs."family-controls-relay"}/bin/family-controls-relay --config ${relayConfig} --bot-token-file ${botTokenPath}";
        Restart = "on-failure";
        RestartSec = "5s";
      };
    };
  };
}
