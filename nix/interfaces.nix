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
          raw = json.generate "${user}.json" settings.settings;
          validated = pkgs.runCommand "validated-${user}.json" { } ''
            ${package}/bin/${user} --config ${raw} --check-config
            cp ${raw} "$out"
          '';
          stateDir = settings.settings.stateDir or "/var/lib/${user}";
        in
        lib.mkIf settings.enable {
          users.groups.${user} = { };
          users.users.${user} = {
            isSystemUser = true;
            group = user;
          };
          systemd.tmpfiles.rules = [ "d ${stateDir} 0700 ${user} ${user} -" ];
          systemd.services.${user} = {
            wantedBy = [ "multi-user.target" ];
            after = [ "network-online.target" ];
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
