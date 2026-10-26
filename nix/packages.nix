{
  config,
  self,
  inputs,
  ...
}:
{
  perSystem = { pkgs, ... }: {
    packages =
      builtins.removeAttrs
        (pkgs.callPackage ../pkgs/family-controls {
          matrixPython = (import inputs.matrix-nixpkgs { system = pkgs.stdenv.hostPlatform.system; }).python3;
        })
        [
          "override"
          "overrideDerivation"
        ];
  };
  flake.overlays.default = _: prev: self.packages.${prev.stdenv.hostPlatform.system} or { };
  flake.nixosModules.default = {
    imports = [
      config.flake.nixosModules.agent
      config.flake.nixosModules.backend
      config.flake.nixosModules.interfaces
      config.flake.nixosModules.web
    ];
    nixpkgs.overlays = [ self.overlays.default ];
  };
}
