# family-controls

Local family screen-time policy, a desktop request window, a parent CLI, and a
Telegram adapter. Timekpr enforces schedules and activity budgets on NixOS.
Telegram can be replaced without changing policy or persistence.

## Parent and child workflows

Parents use `/family` for a status card and buttons. Custom durations accept
`25`, `25m`, or `1h 10m`. Custom prompts use Telegram's reply interface, belong to
the parent who opened them, and expire after ten minutes.

| Action | Telegram example | Effect |
| --- | --- | --- |
| Add or deduct budget | `/addtime child 25m`, `/deducttime child 5m` | Changes today's budget, preserving time already used |
| Set total budget | `/setlimit child 90m` | Sets today's total, including consumed time |
| Set remaining budget | `/setremaining child 25m` | Requires fresh usage; sets the remaining allowance |
| Change schedule cutoff | `/until child 19:30`, `/extendaccess child 15m` | Changes today's final schedule endpoint |
| Short session now | `/session child 25m` | Adds budget and a current access window; rounds the endpoint up to minute precision |
| Unlimited budget today | `/unlimited child` | Removes the ordinary play-budget restriction; schedule and manual blocks remain |
| Allow rest of day | `/allday child` | Opens today's schedule and budget, including homework periods; manual blocks remain |
| Restore defaults | `/restore child` | Removes today's budget/schedule overrides, preserving usage and manual blocks |
| Pause and resume | `/block child today`, `/block child 3d`, `/unblock child` | Blocks through the inclusive local date |
| Mute requests | `/mute child`, `/unmute child` | Controls requests separately from access |

Today-only changes expire at midnight in the child device's configured timezone.
Unlimited and all-day buttons explain their scope and require confirmation.
Explicit slash commands perform the named action directly. Native Timekpr daily
allowances are bounded at 86,400 seconds, including when presenting an unlimited
budget; this is an upstream limit on a 25-hour daylight-saving day.

Children can request a duration and optional reason, cancel a pending request,
and see whether it was approved, denied, or expired. Requests remain possible
when the ordinary budget expires. Muted or manually blocked users cannot submit
requests. Parents can approve a preset/custom session or explicitly deny it.
Only one decision can resolve a request. Replaying a button cannot repeat a grant
or approve a newer request.

A status card distinguishes policy application, access, requests, and connectivity.
A saved change can be **pending** while enforcement is unavailable. Unknown or
stale usage is never presented as zero. An unavailable child does not stop request
notifications for other children. Undelivered Telegram commands expire after ten
minutes; parents should refresh status before retrying.

The desktop helper shows warnings, expired access, request outcomes and transport
errors. Socket work runs outside Tk's event loop. The CLI supports the same parent
operations plus history:

```sh
family-controlsctl request-time --minutes 25 --reason 'Finish this level'
family-controlsctl --host child.example:18451 --token-file /run/agent-token \
  --ca-file /etc/family-controls/child-ca.pem --operation-id parent-grant-123 \
  add-time child 25
```

Reuse the **same operation ID, payload and actor** after an uncertain delivery.
Receipts survive process restarts. A new ID means a new command.

## Architecture

The package is under `pkgs/family-controls/src/family_controls`:

- `domain.py`: pure intervals, daily state, requests and effective access decisions.
- `application.py`: serialized commands, validation, request transitions, accounting,
  reconciliation and status. Depends on small protocols in `ports.py`.
- `adapters/`: SQLite, Timekpr D-Bus, logind, HTTPS/Unix sockets, Telegram, CLI and Tk.
- `bootstrap.py`: configuration, process locks, composition and shutdown.

Inbound adapters authenticate the caller and translate input into application
commands. The application commits intent and its operation receipt in one SQLite
transaction, then applies absolute policy through the enforcement port. Failed
application remains pending and is reconciled. Timekpr results and configuration
readback are checked; its CLI exit code is not treated as proof of success.
Periodic reconciliation reads configuration and repairs drift without rewriting
unchanged settings. Status GETs do not write state.

Timekpr is the sole session/activity enforcer, including its configured grace
period and lockout action. The agent does not separately terminate sessions when
an activity budget expires. Homework (`timeWindows.unmetered`) is access within
the allowed schedule, not free gaming: configured PlayTime activities are blocked
during homework periods, even with budget left. Budget-only changes and short
sessions retain that restriction; “Allow rest of day” explicitly removes it. Partial-hour gaps or mixed accounting within
one hour are rejected because Timekpr cannot represent them faithfully.

`countLockedTime` charges observed elapsed intervals only when every graphical
session is locked, within permitted metered access. Native usage is subtracted
from the same interval. Restart, suspend and observation gaps are not guessed.
Charges persist separately from parent adjustments and survive restoring defaults.
Do not combine this option with `trackInactive`; build/startup validation rejects the
ambiguous overlap.

## Adapter API

Parent endpoints are `GET /v1/users/{user}/status`,
`GET /v1/users/{user}/history`, and `POST /v1/users/{user}/{action}`.
Use `Authorization: Bearer …`, `Content-Type: application/json`, and an
`Idempotency-Key` on every POST. `X-Family-Actor` records the identity authenticated
by the trusted parent adapter; the bearer credential authorizes the adapter.
Do not expose that credential to children. An optional `expectedRevision` rejects
a change if the policy has changed since the caller last read it.

| Actions | JSON fields |
| --- | --- |
| `add-time`, `deduct-time`, `reduce-access`, `grant-session` | `minutes`: integer 1–1440 |
| `set-limit`, `set-remaining` | `minutes`: integer 0–1440 |
| `extend-access` | `minutes`, optional `until` in HH:MM |
| `allow-until` | `until` in HH:MM; 24:00 is a valid endpoint |
| `unlimited-today`, `allow-today`, `restore-today`, `unblock`, `mute`, `unmute` | Empty object |
| `block` | Either `today: true` or `days`: integer 1–31 |
| `request-time` | Optional positive `minutes` and `reason` (up to 300 characters) |
| `cancel-request` | `requestId` |
| `decide-request` | `requestId`, `decision`: `deny`, `add-time`, `grant-session`, `unlimited-today` or `allow-today`; positive `minutes` for time grants; optional `reason` |

Responses include current status and the operation's identity/duplicate flag.
HTTP 200 means the current policy is applied; 202 means intent is durably saved
but application is pending. Validation errors use 400, missing authentication
401, forbidden local identity 403, unknown resource 404, conflicting/stale
operations 409, oversized bodies 413, wrong media type 415, and temporary
unavailability 503. A lost response is not evidence of a lost command: retry the
same operation identity. History distinguishes individually applied operations
from pending intents superseded by a later reconciled revision.

The Unix socket exposes only `/v1/status`, `/v1/request-time` and
`/v1/cancel-request`. The peer UID selects the account; there is no client-supplied
username or root-to-first-child fallback. Both transports use the same application
validation and request lifecycle.

## Deployment and migration

Import `nixosModules.default` for the agent/relay options and sops-nix. Actual
users, limits, endpoints, certificates and secrets belong to the consuming
`nix-config` deployment. Set `time.timeZone` explicitly so Timekpr and the agent
share one stable policy timezone. Import `homeManagerModules.default` for countdown and
Timekpr client options; Plasma also uses `homeManagerModules.plasma` with
plasma-manager. Python entry points remain available under their existing names.

The agent binds to `127.0.0.1` by default and opens no firewall port automatically.
For a remote relay, configure `agent.bindHost`, the network/firewall policy, a
certificate matching that hostname, and the relay child's `caFile`. Publicly
trusted certificates can use the system CA store. Redirects and unverified TLS
are rejected. `apiTokenSecret` names a SOPS secret; `apiTokenFile` supports another
runtime secret provider. Keep credentials out of the Nix store.

New bootstrap certificates include SANs for the device hostname and localhost.
Existing certificates/keys are preserved. Legacy CN-only certificates need an
explicit deployment certificate migration; this repository does not rotate them
or disable verification to make them work. Custom certificates use
`agent.tlsCertPath` and `agent.tlsKeyPath`.

The local socket authenticates the kernel peer UID. Its parent directory is owned
by root and is not writable by managed users. The Telegram relay runs as its own
unprivileged user, reads only its configured secrets and authenticates current
group administrators on each action. Relay state defaults to
`/var/lib/family-controls-relay`; the agent retains `/var/lib/family-controls`.

Existing SQLite state and identifiers are retained. Before the v2 schema migration,
`state.sqlite.before-v2` is created using SQLite's backup API. Legacy pending
requests receive stable IDs; original rows and audit records remain. Timekpr work
files are not rewritten or reset. A new user without a Timekpr usage record reports
unknown/pending until its first managed session creates one. Corrupt state is
preserved and reported for recovery, never silently replaced with empty state.

Agent shutdown/crash invokes a systemd stop hook to restore recurring Timekpr
policy; startup and reconciliation reapply still-valid intent. This depends on a
working Timekpr service and systemd. A machine-wide failure is not an atomic
transaction across the two services. Boot rendering restores recurring policy
without resetting usage. Operation receipts are retained to prevent old retries;
audit details default to 365 days of retention.

DNS profile fields describe **desired, externally unverified** state. There is no
DNS provider adapter in this repository. This software also does not claim to
sandbox arbitrary programs, prevent administrator bypass, or replace OS account
and boot security.

## Development and validation

Follow [AGENTS.md](AGENTS.md), including the pytest guidance copied from the
sibling Python project. Use Python 3.12 or newer:

```sh
uv sync --group dev
# Headless tests; the Nix unit check below also runs the real GUI under Xvfb.
env -u DISPLAY uv run pytest --cov=family_controls
uv run ruff check pkgs/family-controls tests
uv run ruff format --check pkgs/family-controls tests
uv run mypy
```

`checks.unit` runs pytest, property tests, local TLS/Unix integration and real Tk
widgets under Xvfb, enforcing at least 70% branch-aware coverage. `checks.lint`
runs Ruff and mypy. `checks.integration` boots two isolated NixOS VMs: a managed
device with real Timekpr/logind/systemd and an unprivileged relay client. They
communicate over verified HTTPS on a private test network. The suite interrupts
that connection, reboots the relay VM and verifies its queued grant applies once
after recovery. Only the external Telegram API is substituted; no real bot token,
parent group or production state is needed. The VMs use 2.25 GiB of guest RAM in
total and shut down after testing. Package builds and module evaluation are
additional checks. A passing build or VM test does not mean a live deployment
was activated.

Run just the VM suite on an x86_64 Linux host with KVM:

```sh
python3 recovery/sources.py run --source . --target checks -- \
  nix build '{flake}#checks.x86_64-linux.integration' --no-link \
  --no-write-lock-file --no-update-lock-file -L
```

The [original review](docs/review/2026-10-18-review.md) records the baseline findings
and product research. The [implementation report](docs/review/implementation.md)
maps those findings to changes, evidence and remaining deployment checks.

## Source access and recovery

Development and writes use private Forgejo; GitHub is a private recovery mirror.
Standalone inputs use Forgejo upstream mirrors. CI requires the
`NIX_FORGEJO_SSH_KEY` Actions secret with read-only access through `nix-builders`.
Public upstream recovery uses HTTPS Git without an API token.

```sh
python3 recovery/sources.py run --source . --target checks -- \
  nix flake check '{flake}' --no-write-lock-file --no-update-lock-file
```

Recovery retains the locked commit/content hash and handles transport outages
only. Authentication and integrity errors stop the operation. Run
`python3 recovery/sources.py update-check --source .` before `nix flake update`;
updates require Forgejo. The recovery/runner helpers are shared copies of the
`nix-config` implementation and should be updated together.
