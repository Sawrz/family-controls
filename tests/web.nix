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
          services.family-controls.interfaces.web = {
            enable = enabled;
            hostName = "family.example.net";
            sslCertificate = "/run/certs/family.pem";
            sslCertificateKey = "/run/certs/family-key.pem";
          };
        }
      ];
    }).config;
  enabled = evaluate true;
  disabled = evaluate false;
  host = enabled.services.nginx.virtualHosts."family.example.net";
in
assert !disabled.services.nginx.enable;
assert enabled.services.nginx.enable && host.forceSSL;
assert host.sslCertificate == "/run/certs/family.pem";
assert pkgs.lib.hasInfix "proxy_ssl_verify on" host.extraConfig;
assert pkgs.lib.hasInfix "error_page 418 =200 /200.html" host.extraConfig;
assert pkgs.lib.hasInfix "proxy_buffering off" host.extraConfig;
pkgs.writeText "family-controls-web-module" host.extraConfig
