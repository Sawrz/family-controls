# Implementation and verification

The hard-cut central architecture replaces the implementation described by the
[2026-10-18 baseline review](2026-10-18-review.md). Work is on
`main`. No live `nix-config`, production credentials,
runtime databases or external chat accounts were changed. `AGENTS.md` and external dependencies are untouched. The user explicitly authorized
changes to this repository’s core package and summed simultaneous device usage.

## Child onboarding and account administration

New children can be declared without a policy seed. The backend persists their
setup state, rejects time grants until confirmation, and sends restricted policy
to fresh devices. Parents confirm allowance and hours or copy another child's
schedule. Existing policy and offline allocations survive configuration changes.

My account separates personal profile, saved language, password management and
verified messaging links from administrator-only household settings. Telegram and
Matrix each expose their own metadata, links and fresh/stale connection state.
Linking requires a service-observed identity followed by browser confirmation;
listing, unlinking, roles and health reporting enforce backend permissions.
OIDC names refresh from validated provider claims on sign-in.

The packaged Python suite passes 245 tests with 80.04% branch-aware coverage;
the separate Matrix SDK runtime test also passes. All 37 browser workflows pass
with 98.21% frontend statement coverage,
including setup conflict recovery, draft isolation between children, keyboard
focus, profile changes and verified link/unlink. Ten real browser-to-device VM
journeys cover first setup and the deployed adapter socket in addition to
enforcement, shared budgets, offline use and restarts. External provider transport
remains simulated. See [account/setup guide](../account-and-household.md) and
[VM journeys](../vm-user-journeys.md).

The onboarding/account build passed `nix flake check --offline -L`, including both
VM suites. Flake evaluation also passed for both supported architectures with
`--all-systems --no-build`; no ARM runtime execution is claimed.

A follow-up found that stored `request.changed` events omitted the request data
expected by both messaging adapters. Events now include the request snapshot.
Regression coverage synchronizes a real device request, delivers its stored event
to both adapters and checks cancellation. The central VM additionally checks both
authenticated SSE subscriptions and outbound group/room payloads, with only the
external messaging transport simulated. The follow-up unit, lint and central VM
checks passed; the VM script completed in 135.47 seconds.

Impeccable's finish review found three setup defects; the fixes passed its
follow-up review with disposition `ship`. Desktop and phone captures were renewed,
and the documenter recorded the retained design system's new settings patterns.


## Declarative enrollment and web deployment

Configured devices authenticate with unique bearer tokens over verified HTTPS.
The server is provisioned with only precomputed SHA-256 hashes and authorized
account mappings; the first valid connection enrolls automatically. Host metadata
can be exported with `lib.trustedDevicesFromHosts`, without exporting secrets or
requiring a shared repository or secret provider. Manual invitation enrollment
remains available for hosts outside declarative trust.

Rotation and removal invalidate prior credentials while preserving accounting,
policy and command state. A removed host's reservations remain bounded by the
existing offline allocation rules. Configuration-managed device rows explain
where access is changed and omit inapplicable manual approval controls. Desktop
and phone captures were visually reviewed after the VM run.

The web module now provides `listenAddress`/`listenPort` for an internal HTTP
listener. The deployment owns external TLS and access restrictions; `publicUrl`
remains the external HTTPS origin. Assets, API, OIDC callback routing and SSE are
preserved through both proxy hops. The package overlay contributes no packages
on unsupported platforms; i686 package-set evaluation is covered explicitly.

The earlier declarative-enrollment verification passed all 11 x86_64 checks with
`nix flake check --offline -L`. Its packaged suite passed 225 tests with 79.45%
coverage; the separate Matrix runtime check
passed its SDK test. All 34 browser workflows passed through the external HTTPS
proxy and internal HTTP listener. Nine real browser-to-device VM journeys passed
in 263.28 seconds, including derived assignments, token rotation, removal and
retained usage. The central VM suite also passed; both supported architectures
passed flake evaluation (`--all-systems --no-build`). No aarch64 VM execution is
claimed. Runtime artifacts are recorded in [VM journeys](../vm-user-journeys.md).

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
manual registration consumes a short-lived token and remains pending until an admin
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
| Packaged pytest suite | 225 passed; one Matrix SDK test runs separately |
| Branch-aware coverage | 79.45%, above the 70% gate |
| Actual encrypted Matrix SDK runtime | 1 passed on Python 3.14, including crypto-store shutdown |
| Full x86_64 `nix flake check` | All 11 checks passed, including both VM suites, packages, interfaces and desktop wiring |
| x86_64/aarch64 output evaluation | Passed with `--all-systems --no-build`; ARM runtime not tested |
| Configured Ruff checks, mypy, Nix formatting and whitespace | Passed |
| Earlier locked-dependency advisory audit | 49 Python packages and npm lock audited with no known vulnerabilities reported; dependency locks are unchanged in this implementation |
| Parent web browser workflows | 34 passed against the Nix-built package through external HTTPS and internal HTTP |
| Browser-to-device VM journeys | 9 passed in 263.28 seconds across three fresh NixOS guests, with real Timekpr enforcement and native child UI |
| Earlier frontend execution coverage | 98.73% statements, 88.49% branches; not remeasured for this change |
| Web static checks | Svelte/TypeScript: zero errors or warnings; formatting and production build passed |
| Web accessibility and layout | Axe WCAG A/AA and overflow checks passed at desktop, 390px and 320px, including German |

The web interface's earlier independent Impeccable review scored scope labeling, allowance
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
- Ruff formatting/configured checks,
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
  Ten ordered journeys cover automatic declarative enrollment, first child setup,
  personal linking and household integration visibility, a custom grant with
  lost-response retry, five/one-minute warnings and a child request, real activity
  termination at exhaustion, extensions/unlimited/blocking, queued delivery
  through a backend restart, routine persistence, simultaneous usage, weekly reports
  bounded offline credit on both computers, and credential rotation/removal.
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

## Chat workflow redesign (October 2026)

Chat adapters now share account language preferences, household/group fallbacks,
child display names, minute/date formatting and concise delivery receipts.
Telegram uses nested menus with explicit child selection and bulk confirmation;
Matrix supports equivalent commands and replies to request messages. Decisions,
cancellations and expiry update existing request cards across interfaces.

Verification includes language authorization and inheritance, owner-bound and
idempotent bulk retries, custom menu journeys, Matrix reply identity, stale
request rejection, recovery for unlinked senders, shared child names and browser
settings. The central VM journey exercises both outbound notification adapters,
Matrix decisions updating both original messages over SSE, and device application.
External chat services are simulated; no live household messages are sent.
See [Chat interfaces](../chat-interfaces.md) for behavior and deployment defaults.

Final validation: `nix flake check --offline -L` passed all x86_64 checks:
261 Python tests passed (one skipped), 80.23% branch-aware coverage, 39 browser
journeys, Matrix SDK runtime verification, central adapter/device VM integration,
and 10 deployed browser-to-device VM journeys. All-system evaluation also passed
with `nix flake check --offline --all-systems --no-build`; aarch64 was evaluated,
not executed. Impeccable scored both requested localization/copy fixes resolved.
