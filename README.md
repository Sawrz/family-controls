# family-controls

Family screen-time controls with a central FastAPI backend, autonomous NixOS
agents, Telegram and encrypted Matrix interfaces, a bilingual parent web interface,
and a child desktop helper. One installation manages one household.

## Architecture

The reusable Python code lives in `pkgs/family-controls/src/family_controls`.

- `domain.py`, `application.py`, and `ports.py` define local policy, requests,
  accounting and enforcement boundaries without a network framework.
- `backend/` coordinates accounts, approval, desired policies and durable commands
  through storage, password and clock ports.
- `adapters/` implements FastAPI, SQLite, OIDC, Timekpr, logind, Telegram, Matrix,
  CLI and the desktop. Composition roots supply the concrete adapters.
- Agents open outbound HTTPS connections to the backend. There is no device
  management TCP listener. A UID-authenticated Unix socket handles child requests.

The backend owns desired recurring policy, accounts, device approval, parent
commands and their delivery history. Each device owns observed usage and applied
policy. A queued backend change is not reported as applied until the device
acknowledges it. Offline devices keep enforcing their cached policy; stale usage
is displayed as unknown. The cache also supplies boot-time Timekpr configuration.

REST/JSON carries commands and reports. Persisted SSE events wake consumers;
each interface and device stores its own cursor. Reconnects replay unacknowledged
events. Command IDs and local transactional receipts prevent duplicate grants,
including a crash after local commit but before acknowledgement. External chat
notifications have at-least-once delivery: Telegram can repeat a notification if
its send succeeds but the acknowledgement is lost. Matrix uses stable transaction
IDs. Streams reconnect periodically so backend shutdown remains bounded.

The flake uses dendritic flake-parts modules in `nix/`. Core, backend, agent,
Telegram, Matrix, web, CLI, desktop and renderer are separate package outputs.
The Matrix runtime uses a separately pinned nixpkgs input for matrix-nio's
Vodozemac encryption backend; insecure Olm is not enabled.

## Parent and child workflows

The [parent web interface](docs/web-interface.md) supports phone and desktop use,
custom time changes, request decisions, schedules, history and administration.
Enable it with `services.family-controls.interfaces.web.enable`; its static
SvelteKit package shares an HTTPS origin with the API through Nginx.

Telegram `/family` displays status and buttons. Matrix `!family` displays status;
use the same command names with `!` instead of `/`. Durations accept `25`, `25m`
and `1h 10m`.

| Action | Telegram example | Effect |
| --- | --- | --- |
| Add or deduct time | `/addtime child 25m`, `/deducttime child 5m` | Adjust today's budget while preserving consumed time |
| Set total budget | `/setlimit child 90m` | Includes time already used |
| Set remaining time | `/setremaining child 25m` | Requires fresh device usage |
| Change cutoff | `/until child 19:30`, `/extendaccess child 15m` | Adjust today's final schedule endpoint |
| Immediate session | `/session child 25m` | Adds budget and a current access window |
| Unlimited budget today | `/unlimited child` | Removes the play budget; schedule and manual blocks remain |
| Allow rest of day | `/allday child` | Opens today's schedule and budget, including homework periods |
| Restore defaults | `/restore child` | Removes today's overrides; preserves usage and manual blocks |
| Pause/resume | `/block child today`, `/block child 3d`, `/unblock child` | Applies a manual block through the inclusive local date |
| Mute requests | `/mute child`, `/unmute child` | Changes request permission separately from access |
| History | `/history child` | Shows recent command outcomes |

Today-only changes expire at local midnight. Undelivered commands expire after
ten minutes or midnight, whichever comes first. A disconnected device's command
remains delivery-pending until that device confirms whether it had already
committed it or whether it expired. Restoring defaults does not erase usage.

Children can open **Family Controls** from the application launcher at any time,
request a custom duration with a reason, cancel a pending request and see its
outcome. Native desktop notifications at five minutes and one minute include a
**Request more time** action; the local window also provides a fallback. An
extension rearms warnings. The UI performs socket work outside Tk's event loop.

Requests remain possible after budget expiry, subject to request muting and
manual blocks. Telegram supplies approval buttons. Matrix accepts
`!approve child REQUEST_ID 25m` or `!deny child REQUEST_ID reason`.
Only one parent decision can resolve a request.

## API and CLI

The API is rooted at `/api/v1`; its schema is `/openapi.json`. Parent clients use
`Authorization: Bearer …`. Messaging adapters have separate service credentials
and must supply `X-Family-Actor: telegram:USER_ID` or `matrix:@user:server` for
interactive actions. An administrator links each sender to an approved household
account. Interface credentials cannot approve accounts or devices.

| Route | Purpose |
| --- | --- |
| `POST /auth/register`, `/auth/login`, `/auth/logout` | Password registration and sessions |
| `GET /auth/oidc/{provider}/start` | Start browser-bound OIDC authorization with PKCE |
| `GET /me`, `/children`, `/children/{child}/status` | Identity and device-reported status |
| `POST /children/{child}/commands/{action}` | Queue a parent command with `Idempotency-Key` |
| `GET /children/{child}/history` | Latest 100 commands and outcomes |
| `GET /children/{child}/policy`, `PUT …?revision=N` | Read/update desired recurring policy with revision checking |
| `GET /events?after=N` | Resumable parent SSE stream; also accepts `Last-Event-ID` |
| `GET /accounts`, `POST /accounts/{name}/approve` or `/revoke` | Admin account management |
| `POST /invitations`, `/identities`, `/identities/oidc` | Admin invitations and explicit identity linking |
| `GET /devices`, `POST /devices/{id}/invitation`, `/approve`, `/revoke` | Admin device lifecycle |
| `POST /enroll`, `/devices/{id}/sync`; `GET /devices/{id}/events` | Outbound device enrollment, reports and delivery |

Password and OIDC registration/login each have independent switches. Registrations
require approval by default; identity-bound invitations can preapprove them.
Bootstrap administration is explicitly provisioned by password or OIDC issuer and
subject. Email addresses never automatically link accounts. Passwords use Argon2id;
session/device/invitation credentials are stored hashed. Browser session cookies
are Secure, HttpOnly and SameSite; cookie-authenticated mutations require the
configured origin.

```sh
family-controlsctl --backend https://family.example.net login \
  --username admin --password-file /run/secrets/family-admin \
  --output-token-file /run/user/1000/family-session
family-controlsctl --backend https://family.example.net \
  --token-file /run/user/1000/family-session --operation-id grant-2026-10-26-1 \
  add-time child 25
# On the child's device, authenticated by the kernel peer UID:
family-controlsctl request-time --minutes 25 --reason 'Finish this level'
```

Remote commands return HTTP 202 after durable acceptance. Inspect status/history
for `queued`, `accepted`, `applied`, `superseded`, `failed` or `expired`. Retry an
uncertain delivery with the **same ID, actor and payload**. A new ID means a new
command. Optional `expectedRevision` checks the local state revision on delivery.
The local socket exposes only status, request and cancellation for its peer UID.

## Deployment

This is a hard cut: old modules, options, executables and direct device HTTPS
endpoints are replaced. Update the consuming `nix-config` before activation.
See [deployment and enrollment](docs/deployment.md) for complete configuration,
authentication, secret ownership and approval instructions. No live configuration
or production state is changed by this repository's tests.

Timekpr remains the sole session/activity enforcer. Homework periods block
configured PlayTime activities; budget grants retain that restriction, while
“Allow rest of day” removes it. Same-hour schedule gaps or mixed accounting that
Timekpr cannot represent are rejected. Its daily allowance caps at 86,400 seconds,
including on a 25-hour daylight-saving day.

`countLockedTime` charges observed locked intervals within metered access,
subtracting native usage. Unknown restart/suspend gaps are not guessed.
It cannot be combined with `trackInactive`. The stop hook restores the cached
recurring policy; restart reapplies valid local intent. DNS profile names describe
externally unverified desired state; no DNS provider adapter is included.

## Development and validation

Follow [AGENTS.md](AGENTS.md). Use Python 3.12 or newer:

```sh
uv sync --group dev
env -u DISPLAY uv run pytest --cov=family_controls
uv run ruff check pkgs/family-controls tests
uv run ruff format --check pkgs/family-controls tests
uv run mypy
```

`checks.unit` runs pytest with real Tk under Xvfb, a private notification D-Bus,
local HTTPS, Unix sockets and SQLite, enforcing 70% branch-aware coverage.
`checks.lint` checks Ruff and mypy. `checks.packages` builds every package;
`checks.home-manager` validates desktop wiring. `checks.interfaces` validates
Telegram/Matrix service configurations, and `checks.matrix-runtime` exercises the
Matrix composition root against the packaged encrypted SDK.
`checks.web-module` validates web deployment options; `checks.web-browser` runs
pytest-driven Chromium against HTTPS Nginx, the real API and device synchronization,
including English/German layouts and accessibility. See the
[web development guide](docs/web-interface.md#development-and-verification) for
frontend checks and source-mapped browser coverage.

`checks.web-journeys` runs the parent browser inside a backend VM against a real
child VM, including native notifications, request submission and Timekpr process
enforcement. See [VM user journeys](docs/vm-user-journeys.md) for coverage,
artifacts and a command that forces fresh boots even when Nix has cached a pass.

`checks.integration` boots two isolated NixOS VMs with the real backend, agent,
Timekpr, logind and TLS. It tests approval, duplicate commands, both chat adapters,
network loss, backend reboot, agent crash recovery, child isolation, locked usage,
process enforcement and midnight reset. External Telegram/Matrix servers and the
OIDC provider are simulated; no real accounts or messages are used. Guest RAM is
2.5 GiB. A passing test is not a live deployment activation.

```sh
python3 recovery/sources.py run --source . --target checks -- \
  nix flake check '{flake}' --no-write-lock-file --no-update-lock-file
```

The [baseline review](docs/review/2026-10-18-review.md) records the original
findings and product research. The [implementation report](docs/review/implementation.md)
describes the completed scope and verification boundaries.

## Source recovery

Development uses private Forgejo; GitHub is a private recovery mirror. Standalone
inputs use Forgejo upstream mirrors. CI needs `NIX_FORGEJO_SSH_KEY` with read-only
access through `nix-builders`. Public recovery uses HTTPS Git without an API token.
Recovery retains the locked commit/content hash and handles transport failures;
authentication and integrity failures stop it. Run
`python3 recovery/sources.py update-check --source .` before `nix flake update`.
The recovery/runner helpers are shared with `nix-config` and updated together.
