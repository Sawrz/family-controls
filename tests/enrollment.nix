{ self, pkgs }:
let
  hash = builtins.hashString "sha256" "test-only-token";
  host = {
    config.services.family-controls.agent = {
      enable = true;
      deviceId = "child-pc";
      credentialFile = "/run/credentials/device-token";
      credentialHash = hash;
      accounts.alex.child = "alex";
    };
  };
  trust = self.lib.trustedDevicesFromHosts { pc = host; };
  unsupported = self.overlays.default { } { stdenv.hostPlatform.system = "i686-linux"; };
  i686 = import pkgs.path {
    system = "i686-linux";
    overlays = [ self.overlays.default ];
  };
in
assert unsupported == { };
assert builtins.isString i686.hello.drvPath;
assert
  trust == {
    child-pc = {
      credentialHash = hash;
      accounts.alex = "alex";
    };
  };
assert
  !(builtins.tryEval (
    self.lib.trustedDevicesFromHosts {
      a = host;
      b = host;
    }
  )).success;
pkgs.writeText "family-controls-declarative-enrollment" (builtins.toJSON trust)
