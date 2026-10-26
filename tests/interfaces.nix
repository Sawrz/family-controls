{ self, pkgs }:
let
  evaluate =
    enabled:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      system = pkgs.stdenv.hostPlatform.system;
      modules = [
        self.nixosModules.default
        {
          system.stateVersion = "26.05";
          services.family-controls.interfaces = {
            telegram = {
              enable = enabled;
              botTokenFile = "/run/secrets/bot";
              settings = {
                backend = {
                  url = "https://family.example.net";
                  tokenFile = "/run/secrets/backend";
                };
                parentGroupChatId = "-100123";
                children = [ "alex" ];
              };
            };
            matrix = {
              enable = enabled;
              settings = {
                backend = {
                  url = "https://family.example.net";
                  tokenFile = "/run/secrets/backend";
                };
                children = [ "alex" ];
                homeserver = "https://matrix.example.net";
                userId = "@bot:example.net";
                deviceId = "BOT";
                roomId = "!room:example.net";
                tokenFile = "/run/secrets/matrix";
                trustedDevices."@parent:example.net"."DEVICE" = "fingerprint";
              };
            };
          };
        }
      ];
    }).config;
  enabled = evaluate true;
  disabled = evaluate false;
  services = enabled.systemd.services;
in
assert !(disabled.systemd.services ? family-controls-telegram);
assert !(disabled.systemd.services ? family-controls-matrix);
assert services.family-controls-telegram.serviceConfig.User == "family-controls-telegram";
assert services.family-controls-matrix.serviceConfig.User == "family-controls-matrix";
pkgs.writeText "family-controls-interface-services" (
  builtins.toJSON {
    telegram = services.family-controls-telegram.serviceConfig.ExecStart;
    matrix = services.family-controls-matrix.serviceConfig.ExecStart;
  }
)
