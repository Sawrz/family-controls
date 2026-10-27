# Declarative device enrollment

Each device has a distinct random bearer token. The agent reads it from a runtime
file; the backend receives only its SHA-256 hash and authorized account mappings.
A successful authenticated connection automatically registers and approves the
configured device. No invitation, parent login or manual approval is needed.
Agents always use verified HTTPS. Secret storage and delivery belong to the
installation: SOPS, agenix, a credential service, or an ordinary protected file
all work. The flake does not read or generate secrets during evaluation/build.

See [child setup and account management](account-and-household.md) for first-run
policy confirmation and personal integrations.

## Generate a credential

Run this once per device in a private directory, outside a Nix build:

```sh
family-controlsctl device-credential --output-token-file /secure/growlithe-token
```

The command creates a new file with mode `0600`, refuses to overwrite an existing
file, and prints only `{"credentialHash": "..."}`. It generates 256 bits of
randomness. Deliver the file's contents to the device using your preferred method.
Put the printed hash in the server configuration; do not deliver the token there.
For an existing random token, print its hash with:

```sh
family-controlsctl device-credential-hash --input-token-file /secure/growlithe-token
```

These hashes are for high-entropy tokens, not human passwords. Hashing happens
before deployment. The backend receives the bearer token on authenticated HTTPS
requests but stores only its hash. Never share one token among different devices.

## Device configuration

```nix
{
  imports = [ inputs.family-controls.nixosModules.default ];
  time.timeZone = "Europe/Berlin";
  users.users.lilly.isNormalUser = true;
  services.family-controls.agent = {
    enable = true;
    # Defaults to networking.hostName; keep it stable across rebuilds.
    deviceId = "growlithe";
    backend.url = "https://family.example.net";
    credentialFile = "/etc/family-controls/device-token";
    accounts.lilly.child = "lilly";
    managedUsers.lilly = { }; # Restricted until parent setup.
  };
}
```

`accounts` must cover exactly the local `managedUsers`. Returned backend policies
must match both local usernames and expected child identities. The default local
policy restricts access before setup. Centrally saved policies and bounded
offline allocations take over after sync.
Before the first allocation there is no metered credit. Keep `stateDir` unchanged
when adopting this flow so usage, cached policies and receipts remain intact.
Do not set `enrollmentTokenFile` alongside `credentialFile`.

## Server configuration

```nix
services.family-controls.backend = {
  enable = true;
  trustedDevices.growlithe = {
    credentialHash = "<replace with the printed 64-character lowercase SHA-256 hash>";
    accounts.lilly = "lilly";
  };
  settings = {
    publicUrl = "https://family.example.net";
    # Keep the installation's existing password/OIDC bootstrap settings.
    children.lilly = {
      timezone = "Europe/Berlin";
      # No policy seed needed; parents confirm limits in the interface.
    };
  };
};
```

Use `backend.trustedDevices`, not `backend.settings.trustedDevices`, in NixOS.
The standalone backend JSON uses the top-level `trustedDevices` key with the same
structure. Child assignments are derived from the trusted devices' accounts;
do not repeat them in `children.<child>.assignments`. That field remains available
for manually enrolled devices. Conflicting declarations are rejected. Children
can have no currently assigned device; their stored history is retained.

A second device can map its own username to the same child. Shared computers map
each local username to the appropriate child. Usage remains summed across devices.
Only the server's configuration authorizes mappings; a device cannot claim a new
child or approve itself by supplying a name.

## Derive trust from hosts in one flake

Set the public `agent.credentialHash` alongside each host's `credentialFile` and
`accounts`, then collect just the participating child hosts:

```nix
services.family-controls.backend.trustedDevices =
  inputs.family-controls.lib.trustedDevicesFromHosts {
    inherit (self.nixosConfigurations) growlithe pikachu;
  };
```

The helper exports only device IDs, hashes and account mappings. It rejects
duplicate device IDs and incomplete declarations. It does not export secret paths
or plaintext credentials. Each child's host configuration must be independently
evaluable: do not derive those hosts back from the server's evaluated configuration.
A shared plain inventory is another option. Separate repositories can instead
configure the same public metadata explicitly; no shared repository is required.

## Illustrative SOPS integration

This is consuming configuration, not a Family Controls dependency. Assuming
sops-nix and host decryption identities are already configured, on the device:

```nix
{ config, ... }: {
  sops.secrets.family-controls-device = {
    sopsFile = ./secrets.yaml;
    key = "family-controls/growlithe-token";
    owner = "root";
    mode = "0400";
    restartUnits = [ "family-controls-agent.service" ];
  };
  services.family-controls.agent.credentialFile =
    config.sops.secrets.family-controls-device.path;
}
```

Store the generated token in that encrypted entry for this device. The server
still gets only the public hash from the preceding example and needs no SOPS entry
for the device token. For ordinary files, supply the same runtime path option.
Use path strings, not Nix path literals or `builtins.readFile` on plaintext secrets.

## Rotation, removal and recovery

Generate a new token/hash pair, update the server hash and device file, and restart
the backend after deploying its configuration. The agent reads its credential
file on new requests; restart it to reconnect an existing stream immediately.
There can be a short synchronization interruption while deployments differ.
The old token stops authenticating once the backend loads the replacement hash.

Remove a trusted-device entry and deploy/restart the backend to revoke access.
Existing event streams recheck authentication. Revocation prevents new allocations
and commands reaching that device; it cannot remotely erase an offline agent's
previously allocated credit or stop its cached enforcement. Outstanding allocations
remain reserved until surrendered or the accounting day ends.

Do not delete either side's databases. Rotation and re-enrollment retain usage,
commands and the original enrollment timestamp. Re-adding valid trust allows
reconnection. Removed configuration-managed devices cannot fall back to an old
manually approved credential. Reusing a hostname does not authenticate a replacement
machine without the configured token.

The web administration view labels configuration-managed devices, including those
waiting for their first connection. Their trust is edited through deployment;
manual invitation/approval/revocation controls remain for manually enrolled hosts.
