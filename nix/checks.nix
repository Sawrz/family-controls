{ self, inputs, ... }: {
  perSystem =
    { pkgs, ... }:
    let
      source = pkgs.lib.fileset.toSource {
        root = ../.;
        fileset = pkgs.lib.fileset.unions [
          ../pyproject.toml
          ../pkgs/family-controls/src
          ../pkgs/family-controls/tests
        ];
      };
      python = pkgs.python3.withPackages (ps: [
        ps.pydantic
        ps.pytest
        ps.pytest-cov
        ps.hypothesis
        ps.tkinter
        ps.httpx
        ps.fastapi
        ps.uvicorn
        ps.argon2-cffi
        ps.pyjwt
        ps.cryptography
        ps.dbus-python
        ps.dbus-fast
        ps.pygobject3
      ]);
    in
    {
      checks = {
        unit =
          pkgs.runCommand "family-controls-unit"
            {
              nativeBuildInputs = [
                python
                pkgs.openssl
                pkgs.xorg-server
                pkgs.dbus
              ];
            }
            ''
              cp -r ${source} source
              chmod -R u+w source
              cd source
              export HYPOTHESIS_STORAGE_DIRECTORY=$TMPDIR/hypothesis
              Xvfb :99 -screen 0 1024x768x24 -nolisten tcp &
              xvfb_pid=$!
              trap 'kill "$xvfb_pid"' EXIT
              export DISPLAY=:99
              python3 -m pytest --cov=family_controls --cov-report=term-missing
              touch "$out"
            '';
        lint =
          pkgs.runCommand "family-controls-lint"
            {
              nativeBuildInputs = [
                pkgs.ruff
                (pkgs.python3.withPackages (ps: [
                  ps.mypy
                  ps.pydantic
                  ps.fastapi
                  ps.httpx
                  ps.pyjwt
                  ps.argon2-cffi
                  ps.uvicorn
                ]))
              ];
            }
            ''
              cp -r ${source} source
              chmod -R u+w source
              cd source
              ruff check pkgs/family-controls
              ruff format --check pkgs/family-controls
              mypy
              touch "$out"
            '';
        matrix-runtime =
          let
            matrixPkgs = import inputs.matrix-nixpkgs { system = pkgs.stdenv.hostPlatform.system; };
            matrixRuntime = matrixPkgs.python3.withPackages (ps: [
              ps.pydantic
              ps.httpx
              ps.pytest
              (ps.matrix-nio.override { withVodozemac = true; })
            ]);
          in
          pkgs.runCommand "family-controls-matrix-runtime"
            {
              nativeBuildInputs = [ matrixRuntime ];
            }
            ''
              cp -r ${source} source
              chmod -R u+w source
              cd source
              python3 -m pytest pkgs/family-controls/tests/test_matrix_runtime.py
              touch "$out"
            '';
        interfaces = import ../tests/interfaces.nix { inherit self pkgs; };
        home-manager = import ../tests/home-manager.nix { inherit self pkgs; };
        integration = pkgs.testers.runNixOSTest (import ../tests/nixos.nix { inherit self; });
        packages = pkgs.linkFarm "family-controls-packages" (
          pkgs.lib.mapAttrsToList (name: path: {
            inherit name path;
          }) self.packages.${pkgs.stdenv.hostPlatform.system}
        );
      };
      formatter = pkgs.nixfmt;
    };
}
