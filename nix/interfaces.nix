{ self, ... }: {
  flake.nixosModules.interfaces =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.services.family-controls.interfaces;
      json = pkgs.formats.json { };
      packages = self.packages.${pkgs.stdenv.hostPlatform.system};
      interfaceOptions =
        name:
        {
          enable = lib.mkEnableOption "Family Controls ${name} interface";
          transport = lib.mkOption {
            type = lib.types.enum [
              "local"
              "remote"
            ];
            default = "local";
            description = "Local OS-authenticated socket, or an explicitly remote HTTPS backend.";
          };
          settings = lib.mkOption {
            type = json.type;
            default = { };
            description = "Validated interface settings. Supply runtime credential paths.";
          };
        }
        // lib.optionalAttrs (name == "telegram") {
          botTokenFile = lib.mkOption { type = lib.types.str; };
        };
      service =
        name: settings:
        let
          user = "family-controls-${name}";
          package = packages.${user};
          raw = json.generate "${user}.json" (
            settings.settings
            // lib.optionalAttrs (settings.transport == "local") {
              backend.socketPath = "/run/family-controls-backend/adapters.sock";
            }
          );
          validated = pkgs.runCommand "validated-${user}.json" { } ''
            ${package}/bin/${user} --config ${raw} --check-config
            cp ${raw} "$out"
          '';
          stateDir = settings.settings.stateDir or "/var/lib/${user}";
        in
        lib.mkIf settings.enable {
          assertions = [
            {
              assertion = settings.transport != "local" || config.services.family-controls.backend.enable;
              message = "Local Family Controls interfaces require the backend on this host; select remote transport otherwise.";
            }
          ];
          users.groups.${user} = { };
          users.users.${user} = {
            isSystemUser = true;
            group = user;
            extraGroups = lib.optional (settings.transport == "local") "family-controls-adapters";
          };
          systemd.tmpfiles.rules = [ "d ${stateDir} 0700 ${user} ${user} -" ];
          systemd.services.${user} = {
            wantedBy = [ "multi-user.target" ];
            after = [
              "network-online.target"
            ]
            ++ lib.optional (settings.transport == "local") "family-controls-backend.service";
            wants = [ "network-online.target" ];
            serviceConfig = {
              User = user;
              Group = user;
              ExecStart =
                "${package}/bin/${user} --config ${validated}"
                + lib.optionalString (name == "telegram") " --bot-token-file ${settings.botTokenFile}";
              Restart = "on-failure";
              RestartSec = 3;
              NoNewPrivileges = true;
              ProtectSystem = "strict";
              ProtectHome = true;
              PrivateTmp = true;
              UMask = "0077";
              ReadWritePaths = [ stateDir ];
            };
          };
        };
    in
    {
      options.services.family-controls.interfaces = {
        telegram = interfaceOptions "telegram";
        matrix = interfaceOptions "matrix";
      };
      config = lib.mkMerge [
        (service "telegram" cfg.telegram)
        (service "matrix" cfg.matrix)
      ];
    };
}
