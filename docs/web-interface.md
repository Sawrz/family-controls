# Parent web interface

The web interface is a separate `family-controls-web` package. It contains static
SvelteKit/TypeScript files, with English and German UI text. Nginx serves it under
the same HTTPS origin as the existing FastAPI API. No Node application server,
additional policy store or changes to the core package are required.

## Deploy with a local backend

Import `family-controls.nixosModules.default`, keep the backend configuration,
and add:

```nix
services.family-controls.interfaces.web = {
  enable = true;
  hostName = "family.example.net";
  enableACME = true;
};
```

The backend's `settings.publicUrl` must be `https://family.example.net`. Configure
NixOS ACME terms/contact and DNS normally, or supply `sslCertificate` and
`sslCertificateKey` runtime paths instead of enabling ACME. The module enables
Nginx and redirects HTTP to HTTPS. Configure the host firewall and remote access
according to the installation's network policy. Do not expose the loopback
backend port separately.

The default upstream is `http://127.0.0.1:<backend port>`. Set `backendUrl` and,
if necessary, `backendCAFile` for an HTTPS upstream. Remote upstreams must use
HTTPS; certificate verification remains enabled. Cookies and Origin headers
must reach the backend unchanged.

Public login/registration switches and provider IDs come from the local backend
configuration. `/web-config.json` contains only this public metadata. Passwords,
client secrets and service credentials are never included in it or the bundle.
For a separately hosted backend, explicitly configure matching metadata:

```nix
services.family-controls.interfaces.web = {
  enable = true;
  hostName = "family.example.net";
  backendUrl = "https://backend.internal.example.net";
  enableACME = true;
  authentication = {
    password = { login = true; registration = false; };
    oidc = { login = true; registration = true; };
    providers = [{ id = "home"; label = "Household account"; }];
  };
};
```

That backend still uses the web origin as its `publicUrl`. These metadata options
control presentation; the backend independently enforces authentication policy.
The four password/OIDC login/registration switches remain independent.

## Parent workflows

- **Today:** search and filter children and their assigned computers, inspect reported usage and
  delivery state, and make custom daily changes. The editor distinguishes total
  allowance, remaining time, access cutoff, sessions, unlimited play budget,
  all-day access, restoration, manual blocks and request muting.
- **Requests:** approve an exact pending request with a custom amount or another
  supported decision, or decline with a reason. Competing decisions produce a
  conflict rather than another grant.
- **Schedules:** edit each weekday's allowed and unmetered windows, daily play
  allowance and advanced enforcement settings. Revision conflicts preserve the
  draft and require an explicit reload. Unedited DNS metadata is retained;
  the UI does not claim DNS enforcement.
- **History:** inspect commands, actor, delivery outcome and operation identity.
  The current backend history response does not include command amounts or
  reason text; the web interface does not invent them.
- **Settings:** choose language. Administrators can approve/revoke parent
  accounts and enrolled devices, create invitations/enrollment tokens and link
  Telegram, Matrix or OIDC identities. Bootstrap remains a deployment operation.

Device/account assignments are configured in Nix. Shared computers need separate
OS accounts for different children. A shared OS login cannot distinguish children;
the backend shares each child’s allowance across assigned computers. Simultaneous
metered usage is summed; the Today view exposes per-computer allocations and
delivery, and Weekly usage shows daily totals and device details.
The interface labels every target with its child identifier, computer and account.

## Delivery and authentication

The API's HttpOnly, Secure, SameSite cookie authenticates browser requests and
SSE. The frontend does not store bearer tokens. Language is a local preference;
an uncertain command's ID and payload are retained in session storage for that
parent so a reload/retry cannot grant time twice. Logging in as another parent
does not replay it. A fresh command is never silently created as a retry.

SSE refreshes snapshots after changes; a bounded ten-second refresh also updates
usage, because usage ticks do not generate events. Unknown/stale usage is not
shown as zero. A transport outage hides old remaining-time values. Pending
delivery is distinct from application, and offline devices retain local rules.

OIDC starts at the existing API URL. Nginx serves the static callback page for
HTML navigation at `/api/v1/auth/oidc/<provider>/callback`; that page fetches the
same URL as JSON with the path-scoped OIDC cookie, then returns to the interface.
The browser clears authorization parameters from visible history, and auth paths
are excluded from Nginx access logs. Preserve these routes when supplying another
reverse proxy. Use the committed `web/nginx-locations.conf` as the reference.

The build adds a hash-based Content Security Policy for scripts. Nginx adds
frame denial, no-referrer, MIME protection and no-store response headers.
No third-party fonts, analytics, service worker or remote UI assets are loaded.

## Development and verification

```sh
cd web
npm ci --ignore-scripts
npm run check
npm run lint
npm run build
uv sync --locked
NGINX=/path/to/nginx CHROMIUM=/path/to/chromium uv run pytest
```

The browser fixture starts disposable HTTPS Nginx and a real FastAPI/SQLite
backend. It drives the real agent synchronization/application code with a
deterministic enforcement port. It requires OpenSSL, Nginx and Chromium; it does
not contact production services. The OIDC browser test substitutes the external
provider exchange; protocol validation and actual OS enforcement have separate
backend and VM suites.

To measure frontend execution coverage, start with an empty `web/coverage/raw`
directory, run `WEB_COVERAGE=1 uv run pytest`, then `node tests/coverage.mjs`.
The latter maps Chromium V8 ranges to owned source files and requires at least
70% statement coverage. Dependencies are excluded. Production packages omit
source maps. The browser checks also run axe WCAG A/AA checks and overflow checks
at desktop, 390px and 320px widths, including German labels.

```sh
nix build .#family-controls-web
nix build .#checks.x86_64-linux.web-module
nix build .#checks.x86_64-linux.web-browser
nix build .#checks.x86_64-linux.web-journeys
```

The Nix browser check tests the packaged files and the same proxy configuration
used by deployment. It supplies its own fonts and certificate bundle, with
temporary browser configuration/cache directories.
JavaScript is required for interactive controls; no-JavaScript fallback copy is
included. Deployment to an existing household remains a consuming NixOS change.

The separate [VM user journeys](vm-user-journeys.md) connect the parent browser
to real device enforcement and the child's native request/notification UI.
