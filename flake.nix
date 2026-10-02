{
  description = "Family screen-time control daemons and desktop integration";
  inputs.nixpkgs.url = "git+ssh://git@ssh.git.wrzalek.com/nixos/upstream-nixpkgs.git?shallow=1&ref=nixos-26.05";
  inputs.sops-nix = {
    url = "git+ssh://git@ssh.git.wrzalek.com/nixos/upstream-sops-nix.git?shallow=1";
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
        in
        {
          unit = pkgs.runCommand "family-controls-unit" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            python3 -m unittest discover -s ${./pkgs/family-controls}/tests -v
            touch $out
          '';
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
                    custom.system.features.parentalControls = {
                      enable = true;
                      agent.apiTokenSecret = "test-api-token";
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
            in
            pkgs.writeText "family-controls-service" fixture.config.systemd.services.family-controlsd.serviceConfig.ExecStart;
        }
      );
    };
}
