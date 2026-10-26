# Deployment and enrollment

This release replaces the old direct-to-device API and Nix options. Deploy the
backend, device agent and chosen interfaces together. The bilingual parent web
interface covers daily controls, schedules and administration; see the
[web deployment guide](web-interface.md). The admin API and CLI also cover setup.

For preauthorized hosts, use [declarative bearer-token enrollment](declarative-enrollment.md):
configure the agent's runtime token file and the server's precomputed hash and
account mappings. The invitation workflow below is the alternative for manual
onboarding, not an additional step for declaratively trusted devices.

## Nix layers

Import `inputs.family-controls.nixosModules.default` on each participating host.
It provides `services.family-controls.backend`, `.agent`, and
`.interfaces.telegram`/`.matrix`/`.web`. Each has an independent enable switch and
package. Home Manager uses `homeManagerModules.default` and
`services.family-controls.desktop.enable` for the child UI.

Define the initial child policy once in your deployment and reuse it on server
and device:

```nix
# A value shared by the following modules, for example through a let binding.
policy = {
  playTime = {
    dailyLimitMinutes = 60;
    activities = [ ".*minecraft.*" ".*steam.*" ];
  };
  timeWindows.allowed.ALL = [ "09:00-20:00" ];
  timeWindows.unmetered.ALL = [ "16:00-17:00" ];
  dnsProfiles = {
    restrictedProfile = "restricted";
    homeworkProfile = "homework";
    leisureProfile = "leisure";
  };
};
```

Process masks must match the actual applications on your devices. DNS names are
metadata only. Backend policy is seeded from Nix on first use; subsequent API
policy edits are authoritative and survive rebuilds. Nix continues to define
child/device/user assignments, timezone and OS account settings. Assignment edits preserve saved policy and usage. Removed devices’ reserved credit
remains unavailable until surrendered or the accounting day ends. Before its first
approved allocation, a device has no metered credit. Its persisted allocation and
policy cache support later offline operation; a new day requires a new allocation.

A central host behind an existing HTTPS reverse proxy:

```nix
{
  imports = [ inputs.family-controls.nixosModules.default ];
  services.family-controls.backend = {
    enable = true;
    authentication = {
      password.registration.enable = false;
      password.login.enable = true;
      oidc.registration.enable = false;
      oidc.login.enable = false;
      requireApproval = true;
    };
    settings = {
      publicUrl = "https://family.example.net";
      authentication.bootstrapPasswordFile = "/run/secrets/family-admin-password";
      children.alex = {
        assignments = [
          { device = "kids-pc"; user = "alex"; }
          { device = "kids-laptop"; user = "alex"; }
        ];
        timezone = "Europe/Berlin";
        inherit policy;
      };
    };
  };
  # Add this location to your existing TLS virtual host.
  services.nginx.virtualHosts."family.example.net".locations."/" = {
    proxyPass = "http://127.0.0.1:18450";
    extraConfig = ''
      proxy_buffering off;
      proxy_read_timeout 60s;
    '';
  };
}
```

Configure the virtual host's certificate and firewall in your deployment. The
backend binds loopback by default. Alternatively configure `bindHost`, `port`,
`tlsCertFile` and `tlsKeyFile` for its own TLS listener. Device and remote-interface clients
require verified HTTPS. Local messaging interfaces use the OS-protected socket. For a private CA, configure `backend.caFile` or
CLI `--ca-file`; redirects and environment proxy inheritance are disabled.

On the child host:

```nix
{
  imports = [ inputs.family-controls.nixosModules.default ];
  time.timeZone = "Europe/Berlin";
  users.users.alex.isNormalUser = true;
  services.family-controls.agent = {
    enable = true;
    deviceId = "kids-pc";
    backend.url = "https://family.example.net";
    enrollmentTokenFile = "/run/secrets/family-enrollment";
    managedUsers.alex = policy;
  };
  home-manager.users.alex = {
    imports = [ inputs.family-controls.homeManagerModules.default ];
    services.family-controls.desktop.enable = true;
    services.family-controls.timekprClient.enable = true;
  };
}
```

The agent opens no inbound network port. Managed users receive access to the local
socket, which selects their identity from the kernel peer UID. The optional
Timekpr tray client is independent of the Family Controls launcher and warnings.

All secret values must remain outside the Nix store. Supply runtime **path strings**,
not Nix path literals or `builtins.readFile` of a secret. Configure sops-nix, agenix
or another runtime provider in `nix-config`. Backend secrets must be readable by
`family-controls-backend`; Telegram and Matrix secrets by their corresponding
service users; the enrollment token by root. Local adapters need only their
provider credentials. Remote adapters additionally need a distinct backend
credential shared with the corresponding backend interface entry.

## Bootstrap and manual device approval

The bootstrap password is at least 12 characters. It is read only when creating
the initial administrator; changing its file does not reset an existing account.
For OIDC-only bootstrap, use the next section instead.

```sh
umask 077
family-controlsctl --backend https://family.example.net login \
  --username admin --password-file /run/secrets/family-admin-password \
  --output-token-file /secure/family-session

family-controlsctl --backend https://family.example.net \
  --token-file /secure/family-session \
  api /api/v1/devices/kids-pc/invitation --method POST \
  | jq -r '.token' > /secure/family-enrollment
```

Deliver that token to the device's configured runtime file through your secret
provider. It expires after 15 minutes and is consumed by registration. The agent
creates its own durable credential with mode 0600, submits enrollment and waits.
Inspect the pending device, then explicitly approve it:

```sh
family-controlsctl --backend https://family.example.net \
  --token-file /secure/family-session api /api/v1/devices
family-controlsctl --backend https://family.example.net \
  --token-file /secure/family-session \
  api /api/v1/devices/kids-pc/approve --method POST
```

After approval, remove the enrollment token and set `enrollmentTokenFile = null`.
An already enrolled agent also tolerates the old token path disappearing. Preserve
`/var/lib/family-controls-agent/device-credential` and `state.sqlite` on restart.

To replace a device credential, revoke the device through `/devices/kids-pc/revoke`,
stop its agent, remove only `device-credential`, issue a new enrollment token and
repeat approval. Preserve its policy/usage database. Revocation blocks server
access; the device continues local cached enforcement. Do not approve an unexpected
registration merely because its device ID looks familiar.

## Accounts, OIDC and interface identities

Enable registration only for the methods you intend to offer. Password and OIDC
registration can each be enabled while their corresponding login remains disabled;
registration never bypasses the login switch. Pending and revoked accounts cannot
control the household. Admins manage accounts/devices/identity links; parents can
control children and recurring policies. All approved parents share this household.

For OIDC, add a provider and configure its redirect URI as
`https://family.example.net/api/v1/auth/oidc/home/callback`:

```nix
services.family-controls.backend = {
  authentication.oidc.login.enable = true;
  authentication.oidc.registration.enable = true;
  settings.authentication.providers.home = {
    issuer = "https://identity.example.net/realms/home";
    clientId = "family-controls";
    clientSecretFile = "/run/secrets/family-oidc-client";
  };
};
```

An OIDC-only installation can disable password login, omit
`bootstrapPasswordFile`, and supply
`settings.authentication.bootstrapOIDC = { issuer = "…"; subject = "…"; };`.
The issuer must match a configured provider; use its stable subject claim for the
intended administrator. Bootstrap never promotes the first arbitrary visitor.
With the web interface enabled, choose the provider on its sign-in page. Its
callback completes authentication with an HTTP-only session cookie and returns
to the parent interface, or explains that approval is pending. Direct API clients
can start at `/api/v1/auth/oidc/home/start` and request the callback as JSON.

`GET /api/v1/accounts` lists pending accounts, including OIDC issuer/subject.
Approve with `POST /api/v1/accounts/NAME/approve` and `{"role":"parent"}`.
Invitations use `POST /api/v1/invitations` with `identity`, `method` and `role`;
password identities are usernames, OIDC identities are `issuer#subject`. They
expire after two days and are single use. Password registration supplies
`invitation` in its body; OIDC supplies it to the start URL. Approval remains the
default for registration without an invitation.

Link messaging senders through `POST /api/v1/identities`, for example:

```json
{"interface":"telegram","externalId":"123456789","account":"admin"}
```

For Matrix use `"interface":"matrix"` and the full `@parent:example.net` ID.
Use CLI `api … --method POST --body-file FILE` to submit these JSON bodies.
Existing accounts can explicitly link OIDC through `/api/v1/identities/oidc` with
`issuer`, `subject` and `account`; email claims do not merge accounts. Revoking an
account invalidates future access. Previously committed child policy changes
remain in effect, including changes awaiting device delivery.
The last approved administrator cannot be revoked.

## Optional interfaces

On the backend host, transport defaults to `local`. Nix creates the service users,
socket group and `/run/family-controls-backend/adapters.sock`; kernel peer
credentials select the adapter identity. Public HTTP never accepts that shortcut.
No adapter-to-backend token is required. Parent sender linking still applies.

Telegram:

```nix
services.family-controls.interfaces.telegram = {
  enable = true;
  botTokenFile = "/run/secrets/telegram-bot";
  settings = {
    parentGroupChatId = "-1001234567890";
    children = [ "alex" ];
  };
};
```

Use a private parent group. The bot must be able to inspect current group
administrators, and the parent must be both a group administrator and linked to
an approved household account. `/family` provides the controls.

Matrix, including Element X as the parent's client:

```nix
services.family-controls.interfaces.matrix = {
  enable = true;
  settings = {
    homeserver = "https://matrix.example.net";
    userId = "@family-bot:example.net";
    deviceId = "FAMILYBOT";
    tokenFile = "/run/secrets/matrix-bot-access";
    roomId = "!private-room:example.net";
    children = [ "alex" ];
    encrypted = true;
    trustedDevices."@parent:example.net"."PARENTDEVICE" = "ED25519_FINGERPRINT";
  };
};
```

Provision a dedicated bot account/device with its matching access token. Invite it
to a private encrypted room. Compare parent device fingerprints through a trusted
channel and configure them explicitly; new devices require updating trust. The SDK
refuses sending to unverified devices. Preserve `/var/lib/family-controls-matrix`,
including its encryption store and sync cursor. Do not reuse a parent account's
session or reset the bot's crypto store on every restart. An encryption-setting
mismatch blocks outgoing messages; encrypted mode rejects plaintext commands.

For an adapter on another host, select `transport = "remote"` and set its
`settings.backend = { url = "https://family.example.net"; tokenFile = "/run/secrets/adapter-backend"; };`.
On the backend, configure a matching `settings.interfaces.telegram = {
kind = "telegram"; tokenFile = "/run/secrets/adapter-backend"; };` (or Matrix).
Local mode requires an enabled backend on the same host.

## Shared allowance and upgrade boundary

Each child has an `assignments` list; each device/account pair belongs to exactly
one child. One OS account per child per device is supported; shared computers use
separate OS accounts for different children. Configure the same child policy and
timezone on each assigned device and enroll/approve each device separately.

`backend.settings.allocationSeconds` defaults to 300 (range 60–3600). Smaller
allocations reduce disconnected exposure but require more frequent connectivity.
Idle devices retain up to 30 seconds to start an activity. Unused credit is returned
only after local enforcement has been reduced. A disconnected device keeps its
reservation; offline reductions take effect on reconnect or exhaustion. Native
Timekpr polling and termination grace can produce an overrun, which is counted.
`agent.timekprCountdownSeconds` defaults to 60; the VM tests use 2 seconds. This
is an enforcement grace period, separate from the five/one-minute child warnings.

Timekpr saves its consumed-time counter periodically; the report can lag live use.
Unreported usage remains reserved and is not inferred from a changing limit.
Usage reports retain original dates across offline days. Active time covers sampled
unlocked, non-idle graphical sessions; unmetered active time is a subset of active
time. Neither is added to metered time. Unobserved intervals and pre-installation
days are unknown, not zero. Weekly reports label partial coverage.

This is a hard cut of the child-policy schema. Back up backend and agent state
before deployment. A backend database containing the former singular `device` /
`user` policy shape is rejected with a migration error; this release does not
silently convert or delete it. Convert that policy metadata deliberately before
activation, preserving usage, identities and commands. Do not delete device
accounting state to recover a connection: its daily epoch prevents replay of an
old allocation after state loss. Restore the matching backup; otherwise no new
credit is granted for that device until a new accounting day.

## Operations and verification

State defaults to `/var/lib/family-controls-backend`, `…-agent`, `…-telegram` and
`…-matrix`. Back up each service's state with the service stopped or SQLite's
online backup API; copying only a live `.sqlite` file can omit its WAL. Keep the
Matrix crypto store with its interface state. Backend events and command receipts
are retained for replay and duplicate protection; monitor disk usage. Agent audit
retention defaults to 365 days. The backend is a single-process SQLite service,
not a horizontally replicated deployment.

After activation, check service journals, device approval and `policyDelivery`,
then test a small extension, child request, five/one-minute warning and offline
recovery. VM tests exercise real Timekpr and backend services, but cannot verify
your external accounts, selected process masks, physical suspend or compositor
notification/fullscreen behavior. The repository does not activate `nix-config`.
