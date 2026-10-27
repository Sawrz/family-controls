# Child setup and account management

## First child setup

Declare the child and account assignments in Nix, without supplying daily limits:

```nix
# On the device, alongside its backend URL and runtime credential file:
services.family-controls.agent = {
  accounts.lilly.child = "lilly";
  managedUsers.lilly = { };
};

# On the backend, alongside authentication and trustedDevices:
services.family-controls.backend.settings.children.lilly.timezone = "Europe/Berlin";
```

A new child appears as **Needs setup**. Choose **Set up limits** on Today or under
Household administration → Children. Confirm a daily allowance and allowed hours,
or copy another child's allowance and allowed/unmetered schedule. The simple form
uses the same hours every day; Schedules supports individual weekdays afterward.
Zero minutes is a valid deliberate restriction. Changes are pending until devices
report the applied revision.

A fresh device defaults to no allowed hours and no metered credit. After setup,
the default activity pattern meters all user processes. Advanced recurring policy
settings can limit metering to selected activities. The default is not a hidden
60-minute grant: 60 minutes and 08:00–19:00 are editable form suggestions only.

Setup completion is durable backend state, shared by every parent and interface.
Concurrent confirmations use revision checks; a second confirmation cannot replace
the first. Ordinary time grants cannot bypass unfinished setup. A saved policy
continues offline within the device's existing allocation.

Existing households keep their saved policies and count as set up. Removing an old
Nix seed does not reset the policy. An explicit `children.<name>.policy` remains
an optional first-use seed for installations that want automated policy setup.
Later Nix edits cannot overwrite the saved policy. Keep backend and agent state
directories when upgrading.

## My account

Every approved parent can manage their display name, language, password and
personal messaging links here. OIDC names are managed by the configured identity
provider and refresh on sign-in. Language is saved centrally; the signed-out
screen still uses the browser's local preference.

Password changes require the current password. An OIDC-only account may add a
password when password login is enabled and its OIDC session was created within
the last 15 minutes. A password change signs out the account's other sessions.
Disabling password login also disables password management in this interface.

Telegram and Matrix have separate sections with the configured bot, destination,
observed status and this parent's links. To connect:

1. Select **Connect my account**. The code expires after ten minutes.
2. Send `/link CODE` privately to the Telegram bot, or `!link CODE` in the
   configured Matrix room. Matrix's configured encryption requirements still apply.
3. Return to the web interface and confirm the detected identity. Cancel if it
   is not yours. A code alone never completes the link.

The backend stores only the code hash. Codes are not placed in browser storage.
Reloading retains a detected identity, but an undisplayed code must be cancelled
and replaced. An identity already linked to another parent cannot be reassigned.
Disconnecting removes its messaging authority immediately; it neither revokes
household membership nor disables the shared adapter. Telegram commands still
require administrator membership in the configured parent group.

### Child request notifications

When a device synchronizes a new request, every enabled adapter whose `children`
list includes that child receives the backend event. Telegram posts to
`parentGroupChatId` with approval/denial buttons; Matrix posts to `roomId` with
approval/denial commands. The web interface shows the same central request.
Enabling both adapters sends to both destinations. Personal account linking
authorizes parent actions; it does not change notifications into private messages.
Only the first valid decision can resolve a request. Disconnected devices must
reconnect before their requests can reach parents.

## Household administration

Only administrators can access its API and screens:

- **People:** invitations, approvals, role changes and access removal. The last
  administrator cannot be demoted or revoked.
- **Children:** setup state and configuration-managed account/device assignments.
- **Devices:** enrollment, last contact, per-child delivery and manual approval or
  revocation. Declarative trust remains managed in configuration.
- **Integrations:** Telegram and Matrix sections list all linked parents and
  allow unlinking. Bot/group/room settings remain read-only deployment values.
- **Sign-in methods:** configured providers, login/registration switches,
  approval requirements and explicit administrator OIDC identity linking.

Local Nix adapters export only public destination metadata. Remote deployments
can set `backend.settings.integrationMetadata.telegram.destination` or Matrix
`bot`/`destination` strings; these do not authorize an adapter. Health reports
are accepted only from its existing OS-authenticated socket or remote credential.

**Connected** means a successful Telegram poll or Matrix sync was reported.
**Connection problem** means the adapter reported a failed check. Reports older
than 90 seconds become **Unknown**, even if the process is still running.
**Not configured** means the backend authorizes no adapter of that type.
Last successful contact remains visible during an outage. These observations do
not claim that every notification was delivered. No credential paths, tokens or
raw provider error responses are exposed by these screens.

## API boundaries

- `POST /api/v1/children/CHILD/setup?revision=N`: confirm initial limits.
- `PUT /api/v1/me/profile`, `POST /api/v1/me/password`: personal settings.
- `GET /api/v1/me/connections`: own links and pending linking challenges.
- `POST` / `DELETE /api/v1/me/connections/KIND/challenge`: start/cancel linking.
- `POST /api/v1/me/connections/KIND/confirm`: confirm the detected identity.
- `DELETE /api/v1/me/connections/KIND?external_id=ID`: disconnect an own link.
- `GET /api/v1/integrations`, `DELETE /api/v1/integrations/KIND/links?external_id=ID`:
  administrator overview and unlinking.
- `POST /api/v1/adapter/link`, `POST /api/v1/adapter/health`: adapter-only reports.
- `PUT /api/v1/accounts/NAME/role`: administrator role management.
- `GET /api/v1/authentication`: public provider metadata for signed-in parents.

Adapter sender impersonation headers cannot access personal account-management
routes. Messaging authority and household-administrator authority stay separate.
