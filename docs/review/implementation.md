# Implementation and verification

The hard-cut central architecture replaces the implementation described by the
[2026-10-18 baseline review](2026-10-18-review.md). Work is on
`feature/shared-child-usage`. No live `nix-config`, production credentials,
runtime databases or external chat accounts were changed. `AGENTS.md` and external dependencies are untouched. The user explicitly authorized
changes to this repository’s core package and summed simultaneous device usage.


## Shared allowance implementation

A child’s policy targets a validated list of device/account assignments. The
backend reserves finite daily credit transactionally before delivery; devices
persist cumulative consumption, allocation and surrender counters. Duplicate and
out-of-order reports cannot restore credit. Offline or revoked devices keep their
reservation until a confirmed return or day rollover; native polling overruns are
charged rather than rejected. No unused-time timeout can duplicate the budget.

Timekpr’s explicit consumed-time counter is authoritative for metered use. Its live
remaining value and configured limit can be out of sync after a policy change;
the implementation never subtracts those to infer consumption. Unreported usage
stays reserved. Native counter resets and lost accounting-state epochs have
regression coverage. Child requests created or cancelled during a synchronization
round trip survive the response without overriding a parent decision.

Separate daily records support indexed seven-day reports. Active graphical use and
unmetered active use are sampled separately, with unknown coverage explicit.
Usage is summed across devices; grants are applied only once. Parent Today shows
shared balance and per-device delivery/allocation. Weekly usage supports numeric
daily totals, device details, navigation, refresh, failures and empty households.
The native child window shows local usage, local spendable time and the last known
shared balance.

Local chat adapters use an OS-protected Unix socket and kernel peer UIDs; public
HTTP headers cannot impersonate that channel. Nix creates the users/group/socket
wiring automatically. Remote adapters still require explicit HTTPS credentials.
The central VM executes Telegram as its configured local service user and Matrix
through its remote credential, with external provider transport simulated.

Deployment is a hard cut: legacy singular device/user policy metadata must be
converted deliberately before activation; startup refuses it without deleting
state. See [deployment](../deployment.md) and [accounting contract](../shared-usage-design.md).

## Delivered scope

The hexagonal local policy core is retained. A central application layer now owns
accounts, approved devices, desired recurring policies and durable command
coordination. FastAPI, SQLite, Argon2, OIDC, outbound agent synchronization,
Telegram, Matrix, CLI and desktop code implement the relevant ports. Each runtime
has its own Nix package and enable/configuration layer. The flake uses dendritic
flake-parts feature files.

REST commands and reports are paired with persisted, resumable SSE streams.
Devices enforce cached policy offline and acknowledge applied results. Device
registration consumes a short-lived token and remains pending until an admin
approves it. Scoped credentials can be revoked and replaced by reenrollment.
Command identity survives backend/device restarts and uncertain delivery. Parent changes commit once centrally and remain pending until assigned devices
acknowledge the policy. Revoking a parent blocks future actions without undoing
already committed changes, including ones awaiting delivery.

Password and OIDC registration/login have four independent switches. Approval is
the default; identity-bound invitations can preapprove. Bootstrap is explicit,
including OIDC-only administration. OIDC uses browser-bound state, nonce, PKCE,
issuer/audience/authorized-party/signature/expiry validation, and explicit account
links. Email does not confer account ownership. Messaging senders map to approved
household accounts and adapters cannot perform administrative approvals.

Custom durations, total and remaining budgets, cutoff changes, immediate sessions,
unlimited budget today, all-day access, restoration, blocks, request muting,
approval/denial and history share the application contract. Five-minute and
one-minute native notifications include a request action; an application launcher
opens the child request UI at any time. Tests cover notification delivery on a
private D-Bus and actual Tk widgets under Xvfb.

The parent web UI implements the approved family-planner design in SvelteKit and
TypeScript, with English/German, responsive controls, shared-account rows,
schedule editing and household administration. It uses the central API through
an independent Nix-packaged static interface; see [web interface](../web-interface.md).
See [deployment](../deployment.md) for the new Nix options and enrollment flow;
old interfaces/options are intentionally not preserved.

## Baseline finding disposition

| Area | Implementation and evidence |
| --- | --- |
| TLS and exposure | Verified outbound HTTPS, CA/hostname checks, no redirects or inherited proxy settings; no inbound device management port. Real TLS tests and VM traffic. |
| Identity and privilege | Kernel UID selects the child account; approved accounts select parent identity. Separate unprivileged backend/interface services and scoped credentials. |
| Concurrency and retries | Per-user command serialization, transactional state/receipts, ordered pending commands, independent stream cursors and retry-safe request decisions. |
| Validation | Pydantic configuration/wire models, action-specific validation, bounded inputs, secret-safe validation errors and build-time configuration checking. |
| Enforcement | Timekpr D-Bus results and configuration readback checked. Pending/applied states stay distinct, including failed recurring-policy delivery. |
| Accounting and time | Fresh usage required for remaining-time changes; stale/unknown usage remains unknown. Measured locked intervals, no inferred suspend gaps, local-midnight expiry and DST tests. |
| Recurring/temporary policy | Cached backend policy survives offline boots. Reconciliation repairs drift; managed stop/boot closes metered access until durable credit is restored; daily overrides expire. |
| Desktop | Real window class, request launcher, native threshold notifications, background I/O, visible request outcomes and UI-thread resource cleanup. |
| Efficiency | Indexed pending/history queries, change-driven notifications, bounded queues/workers, limited concurrent Argon2 work and change-only Timekpr setters. |
| Matrix encryption | Vodozemac SDK runtime, explicit trusted-device fingerprints, encryption mismatch refusal, durable inbox/crypto state and resource cleanup. No insecure Olm override. |
| Documentation/maintainability | Shared policy validation and presentation, small ports, separate composition roots, deployment instructions, tests and reproducible flake checks. |
| DNS | Explicitly externally unverified metadata; no unsupported enforcement claim. |

## Validation

Verified on 2026-10-26:

| Check | Result |
| --- | --- |
| Packaged pytest suite | 209 passed; one Matrix SDK test runs separately |
| Branch-aware coverage | 79.00%, above the 70% gate |
| Actual encrypted Matrix SDK runtime | 1 passed on Python 3.14, including crypto-store shutdown |
| Full x86_64 `nix flake check` | All 10 checks passed, including both VM suites, packages, interfaces and desktop wiring |
| x86_64/aarch64 output evaluation | Passed with `--all-systems --no-build`; ARM runtime not tested |
| Ruff, additional S/SIM/C4 rules, mypy, Nix formatting and whitespace | Passed |
| Earlier locked-dependency advisory audit | 49 Python packages and npm lock audited with no known vulnerabilities reported; dependency locks are unchanged in this implementation |
| Parent web browser workflows | 34 passed locally and against the Nix-built package through HTTPS Nginx |
| Browser-to-device VM journeys | 8 passed in 299.09 seconds across three fresh NixOS guests, with real Timekpr enforcement and native child UI |
| Frontend execution coverage | 98.73% statements, 88.49% branches from source-mapped Chromium V8 coverage |
| Web static checks | Svelte/TypeScript: zero errors or warnings; formatting and production build passed |
| Web accessibility and layout | Axe WCAG A/AA and overflow checks passed at desktop, 390px and 320px, including German |

The web interface's independent Impeccable review scored scope labeling, allowance
previews and design provenance resolved, with a final `ship` disposition for those
fixes. Automated accessibility checks do not replace manual assistive-technology
testing. This extension also changes the authorized core and central accounting.
The shared-usage review scored report refresh and empty/removed-child handling
resolved, with a `ship` disposition for those two fixes. The documenter verified
that the existing design system remains intact; pre-existing metadata drift was
reported and left unchanged.

The suite includes:

- Pytest with branch coverage required to reach 70%, warnings treated as errors,
  fixtures, parametrization, monkeypatch and property tests. No `unittest.mock`,
  warning suppressions or dependency source patches.
- Packaged Python 3.13 tests with real HTTPS, Unix sockets, SQLite, Tk/Xvfb and a
  private freedesktop notification service. The encrypted Matrix composition
  root additionally runs against its actual Python 3.14/Vodozemac SDK package.
- Ruff formatting/configured checks, additional production security/style rules,
  mypy and Nix formatting.
- Separate package builds, validated Telegram/Matrix service configurations,
  enabled/disabled module checks and Home Manager wiring fixtures.
- Two isolated x86_64 NixOS VMs: managed device (1,536 MiB) and backend (1,024 MiB),
  real Timekpr/logind/systemd/TLS, enrollment and approval, replay, both chat
  adapters, network outage, backend reboot, agent SIGKILL, sibling UID isolation,
  measured locked usage, observed game-process termination, midnight and stop-hook
  closure. Both guests shut down after the test.
- A separate browser-to-device run with Chromium and HTTPS Nginx on the backend
  VM (2,048 MiB), the native child UI on the managed-device VM (1,536 MiB), and a
  second managed laptop (1,536 MiB) sharing the same child allowance.
  Eight ordered journeys cover browser enrollment/approval, a custom grant with
  lost-response retry, five/one-minute warnings and a child request, real activity
  termination at exhaustion, extensions/unlimited/blocking, queued delivery
  through a backend restart, routine persistence, simultaneous usage, weekly reports
  and bounded offline credit on both computers.
  Screenshots, JUnit results and all three journals are saved in the check output.
  See [VM user journeys](../vm-user-journeys.md) for reproduction and boundaries.
- Hash-locked PyPI runtime advisory audit: 49 packages, no known vulnerabilities
  reported. This is a point-in-time advisory result, not an audit of the entire
  NixOS/Timekpr/desktop closure.

## Verification boundaries

External Telegram and Matrix transports and the OIDC provider are simulated in
workflow tests. The Matrix runtime check uses the real SDK and crypto store but
substitutes homeserver I/O. These checks do not prove interoperability with a
particular live homeserver, identity provider, Telegram group or Element session.
No live messages were sent. The deployment should verify those configured accounts
and their trust/permission settings after activation.

The broader `web-browser` tests use a deterministic enforcement port alongside
real API, SQLite, HTTP agent synchronization and command application. The new
`web-journeys` suite connects browser actions to actual operating-system enforcement
in VMs. The web OIDC test substitutes the external provider exchange while
exercising the browser callback and session-cookie flow.

Home Manager tests use a minimal host option interface; they do not activate a
real Plasma or Wayland session. Physical suspend, compositor fullscreen behavior,
actual application process masks and existing production state require deployment
validation. ARM evaluation does not establish ARM runtime behavior.

Timekpr cannot encode separated/mixed-accounting windows within one hour and caps
a daily allowance at 86,400 seconds. Those inputs are rejected or documented,
not silently approximated. A machine-wide failure is not an atomic transaction
across the backend, agent and Timekpr. Backend receipts/events are retained for
replay; operators must monitor and back up state. Telegram notifications can
repeat after an uncertain send, while repeated commands cannot repeat a grant.

Named holiday schedules, weekday-specific budgets and temporary pause timers
remain optional product backlog. Daily/weekly usage reports are implemented. DNS enforcement needs a provider
contract. The web interface does not add these backend capabilities.
