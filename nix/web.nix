{ self, ... }:
{
  perSystem = { pkgs, ... }: {
    packages.family-controls-web = pkgs.buildNpmPackage {
      pname = "family-controls-web";
      version = "0.3.0";
      src = pkgs.lib.fileset.toSource {
        root = ../web;
        fileset = pkgs.lib.fileset.unions [
          ../web/package.json
          ../web/package-lock.json
          ../web/tsconfig.json
          ../web/vite.config.ts
          ../web/.prettierrc.json
          ../web/src
          ../web/static
        ];
      };
      npmDepsHash = "sha256-nvo3IbaEUjRHtKYh6UjBTHV5XYM6zAdKm44+ydqkxhI=";
      npmFlags = [ "--ignore-scripts" ];
      postBuild = ''
        npm run check
        npm run lint
      '';
      installPhase = ''
        mkdir -p "$out/share/family-controls-web"
        cp -r build/. "$out/share/family-controls-web/"
        find "$out" -name '*.map' -delete
      '';
      meta = {
        description = "Bilingual parent interface for Family Controls";
        license = pkgs.lib.licenses.mit;
        platforms = pkgs.lib.platforms.linux;
      };
    };
  };

  flake.nixosModules.web =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.services.family-controls.interfaces.web;
      backend = config.services.family-controls.backend;
      json = pkgs.formats.json { };
      runtime = json.generate "family-controls-web.json" cfg.authentication;
      snippet =
        lib.replaceStrings
          [ "@assets@" "@runtime@" "@backend@" "@ca@" ]
          [ "${cfg.package}/share/family-controls-web" "${runtime}" cfg.backendUrl cfg.backendCAFile ]
          (builtins.readFile ../web/nginx-locations.conf);
      method = lib.types.submodule {
        options = {
          login = lib.mkOption {
            type = lib.types.bool;
            default = true;
          };
          registration = lib.mkOption {
            type = lib.types.bool;
            default = false;
          };
        };
      };
    in
    {
      options.services.family-controls.interfaces.web = {
        enable = lib.mkEnableOption "Family Controls parent web interface";
        package = lib.mkOption {
          type = lib.types.package;
          default = self.packages.${pkgs.stdenv.hostPlatform.system}.family-controls-web;
          description = "Static web interface package.";
        };
        hostName = lib.mkOption {
          type = lib.types.str;
          description = "Virtual host name for the supplied web routes. The backend publicUrl remains the external HTTPS origin.";
        };
        backendUrl = lib.mkOption {
          type = lib.types.str;
          default = "http://127.0.0.1:${toString (backend.settings.port or 18450)}";
          description = "Internal backend origin. Use HTTPS for a remote backend.";
        };
        backendCAFile = lib.mkOption {
          type = lib.types.str;
          default = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
          description = "CA bundle for an HTTPS backend; certificate verification stays enabled.";
        };
        listenAddress = lib.mkOption {
          type = lib.types.str;
          default = "127.0.0.1";
          description = "HTTP listener address. External TLS termination and access restrictions belong to the deployment.";
        };
        listenPort = lib.mkOption {
          type = lib.types.port;
          default = 18451;
          description = "HTTP listener port, independent of the backend publicUrl.";
        };
        authentication = lib.mkOption {
          type = lib.types.submodule {
            options = {
              password = lib.mkOption { type = method; };
              oidc = lib.mkOption { type = method; };
              providers = lib.mkOption {
                type = lib.types.listOf (
                  lib.types.submodule {
                    options = {
                      id = lib.mkOption { type = lib.types.strMatching "[a-zA-Z0-9_.-]+"; };
                      label = lib.mkOption { type = lib.types.str; };
                    };
                  }
                );
                default = [ ];
              };
            };
          };
          default = {
            password = {
              login = backend.authentication.password.login.enable;
              registration = backend.authentication.password.registration.enable;
            };
            oidc = {
              login = backend.authentication.oidc.login.enable;
              registration = backend.authentication.oidc.registration.enable;
            };
            providers = map (name: {
              id = name;
              label = name;
            }) (builtins.attrNames (backend.settings.authentication.providers or { }));
          };
          description = "Public sign-in metadata; inherited from a local backend. Match the remote backend when hosted separately.";
        };
      };
      config = lib.mkIf cfg.enable {
        assertions = [
          {
            assertion =
              builtins.match "https://[^/[:space:]]+|http://(127[.]0[.]0[.]1|localhost)(:[0-9]+)?" cfg.backendUrl
              != null;
            message = "The web backend must be an HTTPS origin or a loopback HTTP origin.";
          }
        ];
        services.nginx.enable = true;
        services.nginx.virtualHosts.${cfg.hostName} = {
          listen = [
            {
              addr = cfg.listenAddress;
              port = cfg.listenPort;
              ssl = false;
            }
          ];
          extraConfig = snippet;
        };
      };
    };
}
