{
  description = "Family screen-time control daemons and desktop integration";
  inputs.nixpkgs.url = "git+ssh://git@ssh.git.wrzalek.com/homelab/upstream-nixpkgs.git?shallow=1&ref=nixos-26.05";
  inputs.sops-nix = {
    url = "git+ssh://git@ssh.git.wrzalek.com/homelab/upstream-sops-nix.git?shallow=1";
    inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs =
    {
      self,
      nixpkgs,
      sops-nix,
    }:
    let
      each = nixpkgs.lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
      ];
    in
    {
      overlays.default =
        final: prev:
        let
          packages = prev.callPackage ./pkgs/family-controls { };
        in
        {
          inherit (packages)
            family-controlsd
            family-controls-sessiond
            family-controls-relay
            family-controlsctl
            family-controls-render
            ;
        };
      packages = each (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        builtins.removeAttrs (pkgs.callPackage ./pkgs/family-controls { }) [
          "override"
          "overrideDerivation"
        ]
      );
      nixosModules.default = {
        imports = [
          sops-nix.nixosModules.sops
          ./modules/nixos/parental-controls.nix
          ./modules/nixos/telegram-relay.nix
        ];
        nixpkgs.overlays = [ self.overlays.default ];
      };
      homeManagerModules.default = { pkgs, ... }: {
        imports = [
          ./modules/home-manager/countdown.nix
          ./modules/home-manager/timekpr-client.nix
        ];
        _module.args.familyControlsPackages = self.packages.${pkgs.stdenv.hostPlatform.system};
      };
      homeManagerModules.plasma = import ./modules/home-manager/plasma.nix;
      checks = each (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonSource = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./pyproject.toml
              ./pkgs/family-controls/src
              ./pkgs/family-controls/tests
              ./tests/relay_vm.py
            ];
          };
        in
        {
          unit =
            pkgs.runCommand "family-controls-unit"
              {
                nativeBuildInputs = [
                  (pkgs.python3.withPackages (ps: [
                    ps.pydantic
                    ps.pytest
                    ps.pytest-cov
                    ps.hypothesis
                    ps.tkinter
                  ]))
                  pkgs.openssl
                  pkgs.xorg-server
                ];
              }
              ''
                cp -r ${pythonSource} source
                chmod -R u+w source
                cd source
                export HYPOTHESIS_STORAGE_DIRECTORY=$TMPDIR/hypothesis
                Xvfb :99 -screen 0 1024x768x24 -nolisten tcp &
                xvfb_pid=$!
                trap 'kill "$xvfb_pid"' EXIT
                export DISPLAY=:99
                python3 -m pytest --cov=family_controls --cov-report=term-missing
                touch $out
              '';
          lint =
            pkgs.runCommand "family-controls-lint"
              {
                nativeBuildInputs = [
                  pkgs.ruff
                  (pkgs.python3.withPackages (ps: [
                    ps.mypy
                    ps.pydantic
                  ]))
                ];
              }
              ''
                cp -r ${pythonSource} source
                chmod -R u+w source
                cd source
                ruff check pkgs/family-controls tests
                ruff check --select S,SIM,C4 pkgs/family-controls/src/family_controls
                ruff format --check pkgs/family-controls tests
                mypy
                touch $out
              '';
          home-manager = import ./tests/home-manager.nix { inherit self pkgs; };
          integration = pkgs.testers.runNixOSTest (import ./tests/nixos.nix { inherit self; });
          packages = pkgs.linkFarm "family-controls-packages" (
            pkgs.lib.mapAttrsToList (name: path: { inherit name path; }) self.packages.${system}
          );
          module =
            let
              fixture = nixpkgs.lib.nixosSystem {
                inherit system;
                modules = [
                  self.nixosModules.default
                  {
                    system.stateVersion = "25.11";
                    time.timeZone = "UTC";
                    custom.system.features.parentalControls = {
                      enable = true;
                      agent.apiTokenFile = "/run/test-api-token";
                      managedUsers.child = {
                        playTime = {
                          dailyLimitMinutes = 60;
                          activities = [ "game" ];
                        };
                        dnsProfiles = {
                          restrictedProfile = "restricted";
                          homeworkProfile = "homework";
                          leisureProfile = "leisure";
                        };
                      };
                    };
                  }
                ];
              };
              relayFixture = nixpkgs.lib.nixosSystem {
                inherit system;
                modules = [
                  self.nixosModules.default
                  {
                    system.stateVersion = "25.11";
                    custom.services.family.telegramRelay = {
                      enable = true;
                      botTokenSecret = "test-bot";
                      parentGroupChatId = "-42";
                      hostChildren.child = {
                        host = "localhost";
                        user = "child";
                        deviceId = "existing-id";
                        apiTokenSecret = "test-agent";
                      };
                    };
                  }
                ];
              };
            in
            assert
              relayFixture.config.systemd.services.family-controls-relay.serviceConfig.User
              == "family-controls-relay";
            pkgs.writeText "family-controls-services" (
              fixture.config.systemd.services.family-controlsd.serviceConfig.ExecStart
              + "\n"
              + relayFixture.config.systemd.services.family-controls-relay.serviceConfig.ExecStart
            );
        }
      );
    };
}
