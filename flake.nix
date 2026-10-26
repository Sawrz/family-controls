{
  description = "Family Controls: central API, autonomous agents and optional interfaces";
  inputs.nixpkgs.url = "git+ssh://git@ssh.git.wrzalek.com/homelab/upstream-nixpkgs.git?shallow=1&ref=nixos-26.05";
  inputs.matrix-nixpkgs.url = "git+ssh://git@ssh.git.wrzalek.com/homelab/upstream-nixpkgs.git?shallow=1&ref=nixos-unstable";
  inputs.flake-parts = {
    url = "git+ssh://git@ssh.git.wrzalek.com/homelab/upstream-flake-parts.git?shallow=1";
    inputs.nixpkgs-lib.follows = "nixpkgs";
  };
  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      imports = map (name: ./nix + "/${name}") (builtins.attrNames (builtins.readDir ./nix));
    };
}
