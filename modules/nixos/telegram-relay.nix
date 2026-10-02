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
  relayConfig = jsonFormat.generate "family-telegram-relay.json" {
    inherit (cfg) parentGroupChatId;
    hostChildren = mapAttrs (_name: child: {
      inherit (child)
        host
        port
        user
        deviceId
        ;
      apiTokenFile =
        if child.apiTokenSecret == null then null else config.sops.secrets.${child.apiTokenSecret}.path;
    }) cfg.hostChildren;
  };
in
{
  options.custom.services.family.telegramRelay = {
    enable = mkEnableOption "family Telegram relay";

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
    ];

    sops.secrets =
      optionalAttrs (cfg.botTokenSecret != null) {
        ${cfg.botTokenSecret} = { };
      }
      // listToAttrs (
        mapAttrsToList (_name: child: lib.nameValuePair child.apiTokenSecret { }) (
          filterAttrs (_name: child: child.apiTokenSecret != null) cfg.hostChildren
        )
      );

    systemd.services.family-controls-relay = {
      description = "Family controls Telegram relay";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${pkgs."family-controls-relay"}/bin/family-controls-relay --config ${relayConfig} --bot-token-file ${botTokenPath}";
        Restart = "on-failure";
        RestartSec = "5s";
      };
    };
  };
}
