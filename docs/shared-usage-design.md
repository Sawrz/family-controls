# Shared child allowances and usage reporting

Implementation contract. The user authorized changes to this repository’s core
package and additive simultaneous usage. External dependencies and AGENTS.md
remain untouched. Validation results are recorded in the implementation review.

## Scope

- One child identity with multiple device/OS-account assignments.
- One daily allowance and one set of parent decisions for that child.
- Durable daily usage and weekly reports, aggregated and per device.
- A child desktop view of usage, remaining allowance, restrictions and requests.
- Parent views of the shared allowance, delivery per device and weekly usage.
- Local Telegram/Matrix adapters authenticated through OS-protected Unix sockets,
  without a manually provisioned backend credential.

Retain the existing interface design, English/German support, independent Nix
packages and service switches, and one household per installation. External
dependencies and AGENTS.md remain unchanged. No production activation is included.

## Identity and policy

The child is the policy target. Device/account pairs identify where that child's
policy is enforced; they are not separate children. Each pair belongs to exactly
one child. Different children on one computer use separate OS accounts.

The child has a policy timezone and a recurring allowance. Device timezone
configuration must agree with the accounting day used by its assigned policies.
Parent commands act on the shared child allowance. Device-specific delivery and
connectivity remain visible. An extension must not be multiplied by device count.

Nix declares assignments and seeds initial policy. Persisted parent policy edits
remain authoritative across rebuilds. Assignment changes must not reset usage or
make outstanding offline allowances spendable twice.

## Accounting and offline operation

The rule is additive metered usage: ten active metered minutes on each of two
devices consume twenty minutes.

The backend owns the shared allowance and allocation ledger. Devices own their
measured usage and enforce their allocated portion locally. Issuing an allocation
atomically reserves that time from the shared allowance before it is returned to
the device. A retried response cannot issue another allocation.

Disconnected devices can consume only already allocated time. The backend must
not reclaim a reservation merely because the device disappears or an allocation
response is lost. Reclaiming unused time requires a durable device acknowledgement
that the surrendered portion can no longer be spent. This also applies to revoked
devices, reconnects and restarts. A device with no allocation cannot start metered
use offline. Active devices receive up to `allocationSeconds` (default 300); idle devices
retain up to 30 seconds. An idle reserve is surrendered when another active device
needs it and no unreserved credit remains. Native polling/termination grace can
produce an overrun, which remains charged. The default termination grace is 60
seconds; it can be configured independently of allocation size.

At rollover, old allocations cannot authorize use on the new policy day. Late
reports update the original day. Clock changes, DST and suspend must not create
time credit or fictitious activity. Unobserved intervals remain unknown. A local
reboot preserves allocations and consumed counters; missing state must not reset
a managed device to the full household allowance.

Parent reductions and blocks cannot instantly reach disconnected devices. The UI
must distinguish the desired limit, confirmed usage and outstanding allocations;
show pending delivery instead of claiming all devices have stopped.

## Durable usage

Metered usage reads `PLAYTIME_SPENT_DAY` and `LAST_CHECKED` together from
Timekpr’s saved work file (`timekpr.workDir`, default `/var/lib/timekpr/work`).
Its D-Bus spent counter omits the date and can still contain yesterday’s usage
after midnight. A previous-day file contributes zero to today; future-dated,
missing, incomplete or invalid files are retried without changing accounting.
Observations crossing a policy-day boundary are also retried. Live remaining time
and configuration readback must not be subtracted to infer consumption.
Reports can lag the counter flush; unreported usage remains reserved. Surrender
limits the return to live spendable credit and newly issued credit that has not
yet been installed. Same-day counter resets preserve already charged usage.

This prevents new rollover errors; it does not rewrite existing usage ledgers.
For an already affected day, a parent can set the remaining time to the intended
balance after deploying the corrected backend. Setting remaining time calculates
the total as recorded usage plus the requested remainder, even when usage exceeds
the old allowance. That restores access but leaves historical usage unchanged.
On older backends, set the **total** to recorded usage plus the intended remaining
time instead; their remaining-time command can undergrant an overdrawn budget.

Store reported usage independently of the latest status snapshot and independently
of command history. Use validated, versioned reports with stable identifiers and
durable progress so retransmission, duplicate batches and out-of-order delivery
cannot double count or erase usage. Persist a batch and its acknowledgement in one
transaction. Reject reports for another device's assignments.

Index usage by child, policy day and device. Weekly queries must be bounded and
must not scan an indefinitely growing command/event history. Explicitly distinguish:

- Active computer time: observed active, unlocked computer use.
- Metered play time: usage charged against the configured allowance.
- Unmetered time: observed active use during explicitly unmetered periods.

These metrics need defined sampling and overlap semantics; do not infer historical
usage by subtracting current remaining time from a limit that parents can change.
Old installations have no complete usage ledger. Display unavailable historical
days honestly rather than reconstructing or filling them with zeros.

## Interfaces

The parent Today view groups by child, shows one shared allowance and expands to
device/account status. Change previews and request decisions use that shared
scope. Show how much confirmed time was used, what is still available, and when
offline devices make the balance uncertain.

Add a weekly usage view with week navigation, daily totals and device breakdowns.
Provide readable numeric values alongside charts and identify incomplete days.
Keep controls usable by keyboard and on narrow German layouts.

Extend the child desktop launcher with today's used time, shared remaining time,
the time currently available on this computer, applicable restrictions and request
status. Never present a stale shared balance as guaranteed offline availability.
Keep five-minute and one-minute warnings consistent with enforceable local time.

Local adapters use a separately protected Unix-socket listener. OS identity must
select the permitted adapter, not an untrusted HTTP header. Public TCP listeners
must never accept this trust shortcut. Parent identity links and role checks still
apply. Nix configures socket access automatically. Explicitly remote adapters may
retain separately configured credentials and verified HTTPS.

## Acceptance evidence

Unit and protocol tests must cover allocation conservation, concurrent issuance,
lost responses, duplicate/out-of-order reports, permission boundaries, usage across
midnight/DST, restart persistence, offline exhaustion, surrender and reassignment,
and parent changes with outstanding allocations. Use pytest and monkeypatch;
maintain the existing coverage gate without testing dependency implementation.

Browser and native UI journeys must cover shared changes, request decisions,
weekly navigation, device breakdowns, missing data and English/German layouts.

Run a backend VM and at least two real managed-device VMs for one child. Prove
that switching computers does not reset the budget, simultaneous use follows the
chosen rule, extensions apply once, isolated devices cannot duplicate the budget,
and reports reconcile after backend/agent restarts. Include a sibling to verify
identity isolation. Exercise the real child UI and real Timekpr enforcement.

Verify local adapter socket access from the intended service user, denial for an
unrelated user, and denial of spoofed local identity through the public API. Check
Nix builds and enabled/disabled interface configurations. Save journals, screenshots
and test reports and state any remaining physical-hardware or provider boundaries.
