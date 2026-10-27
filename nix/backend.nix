{ self, ... }: {
  flake.nixosModules.backend =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.services.family-controls.backend;
      json = pkgs.formats.json { };
      raw = json.generate "family-controls-backend.json" (
        cfg.settings
        // {
          trustedDevices = cfg.trustedDevices;
          integrationMetadata =
            (cfg.settings.integrationMetadata or { })
            // lib.listToAttrs (
              lib.concatMap
                (
                  name:
                  let
                    adapter = config.services.family-controls.interfaces.${name};
                  in
                  lib.optional (adapter.enable && adapter.transport == "local") {
                    inherit name;
                    value =
                      if name == "telegram" then
                        {
                          destination = adapter.settings.parentGroupChatId or null;
                        }
                      else
                        {
                          bot = adapter.settings.userId or null;
                          destination = adapter.settings.roomId or null;
                        };
                  }
                )
                [
                  "telegram"
                  "matrix"
                ]
            );
          adapterSocketPath = "/run/family-controls-backend/adapters.sock";
          localAdapters = lib.listToAttrs (
            lib.concatMap
              (
                name:
                lib.optional
                  (
                    config.services.family-controls.interfaces.${name}.enable
                    && config.services.family-controls.interfaces.${name}.transport == "local"
                  )
                  {
                    name = "family-controls-${name}";
                    value = name;
                  }
              )
              [
                "telegram"
                "matrix"
              ]
          );
          authentication = (cfg.settings.authentication or { }) // {
            password = {
              registration = cfg.authentication.password.registration.enable;
              login = cfg.authentication.password.login.enable;
            };
            oidc = {
              registration = cfg.authentication.oidc.registration.enable;
              login = cfg.authentication.oidc.login.enable;
            };
            requireApproval = cfg.authentication.requireApproval;
          };
        }
      );
      validated = pkgs.runCommand "validated-backend.json" { } ''
        ${
          self.packages.${pkgs.stdenv.hostPlatform.system}.family-controls-backend
        }/bin/family-controls-backend --config ${raw} --check-config
        cp ${raw} "$out"
      '';
      stateDir = cfg.settings.stateDir or "/var/lib/family-controls-backend";
      toggle =
        default:
        lib.mkOption {
          type = lib.types.bool;
          inherit default;
        };
    in
    {
      options.services.family-controls.backend = {
        enable = lib.mkEnableOption "Family Controls central API";
        trustedDevices = lib.mkOption {
          type = lib.types.attrsOf (
            lib.types.submodule {
              options = {
                credentialHash = lib.mkOption {
                  type = lib.types.strMatching "[0-9a-f]{64}";
                  description = "SHA-256 of this device's random bearer token, computed before deployment.";
                };
                accounts = lib.mkOption {
                  type = lib.types.attrsOf lib.types.str;
                  description = "Authorized local username to household child identity mapping.";
                };
              };
            }
          );
          default = { };
          description = "Declaratively approved devices. Removing an entry revokes its access; no plaintext device tokens are stored here.";
        };
        settings = lib.mkOption {
          type = json.type;
          default = { };
          description = "Validated backend configuration; secrets must be runtime file paths.";
        };
        authentication = {
          password.registration.enable = toggle false;
          password.login.enable = toggle true;
          oidc.registration.enable = toggle false;
          oidc.login.enable = toggle true;
          requireApproval = toggle true;
        };
      };
      config = lib.mkIf cfg.enable {
        assertions = [
          {
            assertion = !(cfg.settings ? trustedDevices);
            message = "Use services.family-controls.backend.trustedDevices, not settings.trustedDevices.";
          }
        ];
        users.groups.family-controls-backend = { };
        users.groups.family-controls-adapters = { };
        users.users.family-controls-backend = {
          isSystemUser = true;
          group = "family-controls-backend";
          extraGroups = [ "family-controls-adapters" ];
        };
        systemd.tmpfiles.rules = [ "d ${stateDir} 0700 family-controls-backend family-controls-backend -" ];
        systemd.services.family-controls-backend = {
          wantedBy = [ "multi-user.target" ];
          after = [ "network-online.target" ];
          wants = [ "network-online.target" ];
          serviceConfig = {
            User = "family-controls-backend";
            Group = "family-controls-backend";
            ExecStart = "${
              self.packages.${pkgs.stdenv.hostPlatform.system}.family-controls-backend
            }/bin/family-controls-backend --config ${validated}";
            Restart = "on-failure";
            RestartSec = 3;
            NoNewPrivileges = true;
            ProtectSystem = "strict";
            ProtectHome = true;
            PrivateTmp = true;
            UMask = "0077";
            RuntimeDirectory = "family-controls-backend";
            RuntimeDirectoryMode = "0755";
            ReadWritePaths = [ stateDir ];
          };
        };
      };
    };
}
