{ self, ... }: {
  perSystem =
    { pkgs, ... }:
    let
      source = pkgs.lib.fileset.toSource {
        root = ../.;
        fileset = pkgs.lib.fileset.unions [
          ../web/tests
          ../web/pyproject.toml
          ../web/nginx-locations.conf
          ../pkgs/family-controls/src
        ];
      };
      python = pkgs.python3.withPackages (ps: [
        ps.pytest
        ps.playwright
        ps.pydantic
        ps.fastapi
        ps.uvicorn
        ps.httpx
        ps.argon2-cffi
        ps.pyjwt
        ps.cryptography
      ]);
      axeLock =
        (builtins.fromJSON (builtins.readFile ../web/package-lock.json)).packages."node_modules/axe-core";
      axeArchive = pkgs.fetchurl {
        url = axeLock.resolved;
        hash = axeLock.integrity;
      };
      axe = pkgs.runCommand "family-controls-accessibility-checker" { } ''
        tar -xOf ${axeArchive} package/axe.min.js > "$out"
      '';
    in
    {
      checks.web-module = import ../tests/web.nix { inherit self pkgs; };
      checks.web-journeys = pkgs.testers.runNixOSTest (import ../tests/web-vm.nix { inherit self; });
      checks.web-browser =
        pkgs.runCommand "family-controls-web-browser"
          {
            nativeBuildInputs = [
              python
              pkgs.openssl
              pkgs.nginx
              pkgs.chromium
            ];
            WEB_BUILD = "${
              self.packages.${pkgs.stdenv.hostPlatform.system}.family-controls-web
            }/share/family-controls-web";
            CHROMIUM = "${pkgs.chromium}/bin/chromium";
            NGINX = "${pkgs.nginx}/bin/nginx";
            AXE_SCRIPT = axe;
            FONTCONFIG_FILE = pkgs.makeFontsConf { fontDirectories = [ pkgs.dejavu_fonts ]; };
            SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
          }
          ''
                cp -r ${source} source
                chmod -R u+w source
                cd source/web
            export XDG_CONFIG_HOME=$TMPDIR/browser-config
            export XDG_CACHE_HOME=$TMPDIR/browser-cache
            mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
                pytest -q
                touch "$out"
          '';
    };
}
