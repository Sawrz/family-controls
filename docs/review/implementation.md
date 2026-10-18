# Implementation and verification

This follows the [2026-10-18 baseline review](2026-10-18-review.md). The reusable
implementation is on `feature/reliable-family-controls`. No live configuration,
credentials, user/device identities or runtime databases were changed. Existing
source-mirror changes in `flake.nix`, `flake.lock` and `recovery/sources.json` were
preserved. Build, isolated runtime testing and deployment activation remain
separate outcomes.

## Delivered behavior

The application now has a pure policy domain, explicit application use cases and
small ports for storage, enforcement, session observation, time and agent access.
Telegram, HTTPS/Unix, CLI, Tk, SQLite, Timekpr and logind are adapters. Existing
executable names remain available. The implementation deliberately uses fewer
files than the suggested layout where splitting further would add indirection.

Custom durations, total/remaining budgets, specific cutoffs, immediate sessions,
unlimited budget today, access for the rest of the day and restoring defaults all
use the same command contract. Requests have IDs, duration/reason, expiry,
cancellation and one parent decision. Telegram provides presets/custom replies,
all-day confirmation, denial reasons, history, mute/unmute and live status. The
child UI stays responsive during network work and shows request outcomes.

See the [README](../../README.md) for precise semantics, commands, trust setup,
state migration and reproducible checks.

## Baseline finding disposition

| Findings | Implementation and evidence |
| --- | --- |
| R01 TLS | Verified HTTPS, explicit CA support, hostname checks, no redirects/proxy inheritance. Local TLS tests and VM traffic. Existing certificate migration remains deployment work. |
| R02, R21 desktop | Real Tk class at construction, safe close/reopen, background I/O, queued requests and visible failures. Xvfb tests exercise actual widgets. |
| R03 concurrency | Per-user serialization, bounded command lock waits, SQLite transactions and durable operation receipts. Concurrent-grant and restart tests. |
| R04 validation | Strict, action-specific input validation and representability checks before committing. Malformed cutoffs/fields leave state unchanged. Corrupt existing data is preserved for recovery. |
| R05 enforcement results | Structured D-Bus calls, semantic result checks and configuration readback. No CLI exit-code inference. Real VM contract. |
| R06, R16 resource bounds | Socket/body/response limits, connection slots, worker TLS handshakes, transport/D-Bus deadlines, bounded reconciliation concurrency, explicit network exposure. |
| R07 local identity | Kernel peer credentials map to the actual managed account; parent commands unavailable locally. Root-owned non-writable socket directory. Real sibling/UID VM checks. |
| R08 countdown | Current interval cutoff, live counters preferred, zero retained, unknown/stale distinguished. Boundary and telemetry tests. |
| R09 homework | Homework is intersected with allowed access. Configured PlayTime activities are blocked during homework. All-day access explicitly clears today's homework restriction. |
| R10 timezone | Aware policy clock, local-midnight expiry, legacy naive-time migration and monotonic locked accounting. DST day-length and daily-reset tests. |
| R11 temporary policy | Full-week recurring baseline plus today's override; drift repair, boot rendering and stop hook. Midnight, process restart and crash checks. |
| R12 approvals | Opaque expiring buttons, stable operation IDs, exact request IDs, original actor claim. Two-parent and stale-request tests. |
| R13 transport | Durable Telegram inbox/offset, independent retryable updates, expiration of undelivered commands, stable operation identities after lost replies. |
| R14 requests | Pending/approved/denied/cancelled/expired, cooldown, duration/reason, persistent notifications and explicit mute policy. Lifecycle tests. |
| R15 API contract | Exact routes, bounded JSON objects, strict action fields, consistent error statuses and required idempotency keys. Protocol tests. |
| R17 configuration | Pydantic configuration models and Nix build-time validation, bounded values, normalized intervals and explicit incompatible-option errors. |
| R18 accounting/enforcement | Timekpr is the sole session enforcer. Locked usage uses observed monotonic intervals, subtracts native consumption, excludes unknown gaps and persists absolute charges. Concurrent inactive accounting is rejected. |
| R19 efficiency | Read-only status, daily audit pruning, change-only Timekpr setters and periodic drift readback. Expired relay conversations/buttons are pruned. |
| R20 device isolation | Bounded concurrent notification polling, independent durable update retries and handling of unavailable credential files. |
| R22 partial minutes | Budget adjustments operate in seconds; deducting the last 59 seconds works. |
| R23 DNS | Status explicitly says `external-unverified`; no unsupported enforcement claim. A real provider adapter needs a provider/account contract. |
| R24 tests/CI | pytest fixtures/parametrization/Hypothesis, real local TLS/Unix/SQLite/Tk, coverage gate, NixOS VM, lint/type checks and desktop module wiring fixtures. |
| R25, R26 structure | Small ports and composition root; shared boot/runtime encoding; Pydantic configuration; consistent Ruff formatting, types, package metadata and operator documentation. |
| R27 privileges/audit | Unprivileged relay, hardened services, authenticated actor attribution and operation outcomes in history. Root remains necessary for the existing Timekpr administrative boundary. |
| R28 encoding limits | Unrepresentable same-hour gaps/accounting transitions are rejected before persistence and during Nix configuration builds. No silently altered schedule. |

The temporary baseline regression harness was retired after its scenarios moved
into maintained pytest tests. The original review remains historical evidence;
its old line references describe the original commit.

## Completed checks

| Check | Result |
| --- | --- |
| pytest suite, Python 3.12 and packaged Python 3.13 / Xvfb | **153 passed**, **85.49%** branch-aware coverage; warnings treated as errors |
| Two-VM NixOS integration network | **Passed**: real Timekpr/logind, cross-VM verified TLS, sibling identity, activity termination, restart/SIGKILL, midnight and restoration; three relay pytest scenarios cover parent-button replay, network outage and durable recovery after a relay VM reboot |
| Ruff formatting/configured lint and additional production S/SIM/C4 rules | **Passed** |
| mypy | **Passed**, 14 source files |
| x86_64 packages, validated agent/relay configs, NixOS and desktop wiring fixtures | **Passed** through recovery-wrapped `nix flake check` |
| x86_64 and aarch64 output evaluation | **Passed** with `--all-systems --no-build`; no ARM runtime-build claim |
| Locked PyPI runtime dependencies | **No known vulnerabilities found** by pip-audit with required hashes and a fresh advisory cache |
| Patch whitespace | **Passed** (`git diff --check`) |

The dependency audit covers the Python runtime lock, not the complete NixOS,
Timekpr or desktop dependency closure, and is an advisory snapshot rather than a
guarantee that no vulnerability exists.

## Verification boundaries

The Python suite runs on Python 3.12 and the pinned Nix Python 3.13 environment.
Warnings are errors; coverage includes application/adapters and requires at least
70% with branches. No `unittest.mock`, expected-failure masking or dependency
patches are used. Ruff's configured rules, additional production security/style
rules, mypy and Nix formatting are checked.

The NixOS suite boots a managed-device VM (1,536 MiB RAM) and a relay VM (768 MiB)
on an isolated network. The managed device runs real Timekpr, systemd, logind,
TLS, SQLite and the CLI. The relay pytest scenarios run as an unprivileged user,
substitute only the external Telegram API, and connect over verified HTTPS to
the managed device. A firewall-induced outage queues a grant; the relay VM then
reboots and recovers that persisted update without granting time twice. Both
synthetic PAM sessions must start successfully before tests proceed. Tests cover
current-admin attribution, replay, sibling isolation, pending/applied state,
locked usage, an observed activity being terminated, service stop, SIGKILL restart
and rollover to a new local day. The activity test requires Timekpr to detect the
running process before exhausting its budget and checks its termination signal.
Certificates and credentials are generated or synthetic test fixtures; no real
Telegram account or production state is needed. The relay tests exercise the
adapter and durable inbox in separate Python processes, not a live Telegram bot
daemon. Both VMs shut down after the run; the repository retains their reproducible
test definitions and the README includes a command to run them again.

Home Manager wiring fixtures evaluate this repository's exported modules against
a minimal host option interface: enabled/disabled behavior, package injection,
notification/autostart configuration and the Plasma window-class rule. They are
not a claim of real Home Manager activation or a Plasma/Wayland desktop session.

## Remaining deployment checks and product choices

These require the deployment or an explicit product/provider choice:

- **Trust rollout:** choose the reachable device hostname/address, certificate/SANs,
  relay CA paths, firewall exposure and runtime secret ownership in `nix-config`.
  Existing CN-only certificates are intentionally not overwritten.
- **Real Telegram account:** verify the deployed bot is a group administrator,
  update/privacy settings, delivery and rate limits against that account. No live
  Telegram messages were sent during development.
- **Actual desktops/devices:** activate the modules, verify the compositor's
  topmost/fullscreen behavior, real application masks and multi-session behavior,
  and exercise physical suspend/resume. Synthetic clocks and headless logind
  sessions cannot prove those hardware/compositor outcomes.
- **Existing state:** the migration is fixture-tested and preserves identifiers and
  usage. Production databases were not inspected or migrated. Corrupt production
  state needs a separately reviewed recovery using its preserved data/backup.
- **DNS:** select a provider and desired failure/verification behavior before
  implementing its adapter. Desired profile names alone cannot enforce DNS.
- **Platform/backend limits:** aarch64 outputs evaluate successfully, but this
  host has no ARM execution platform/binfmt handler; an ARM builder is needed for
  runtime validation. Timekpr cannot encode multiple separated intervals/accounting
  modes within one hour; native daily allowances cap at 86,400 seconds. These are
  documented backend constraints, not silently approximated capabilities.

The original later-feature proposals—named holiday schedules, weekday-specific
budgets, automatic temporary pauses, usage summaries and a second parent
interface—remain product backlog. Shared-device budgets, rewards, browsing or
location tracking remain deferred because they require additional scope and
privacy decisions. They are not prerequisites for the implemented daily-control
workflows.
