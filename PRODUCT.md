# Family Controls

<!-- impeccable:product-schema 1 -->

## Platform

web

This record guides the upcoming parent web interface. The existing child desktop
helper, Telegram and Matrix interfaces remain part of the wider product.

## Stack

The central backend uses FastAPI, REST/JSON and resumable server-sent events.
The domain and application code follow hexagonal architecture. Nix packages and
services are delivered through one dendritic flake.

The parent interface uses SvelteKit and TypeScript, built as static files and
served with the API under one HTTPS origin. English and German are supported.

## Users

- Parents manage children's daily access, respond to requests and inspect status.
- Household administrators also approve accounts and devices and link identities.
- Children see their remaining time and request extensions through the existing
  local desktop helper.

The parent web interface must serve phones and desktops equally. Neither is a
secondary experience. A child-facing web experience has not been agreed.

## Product Purpose

Help a household manage computer access with understandable rules and convenient
day-to-day exceptions. Parents need to see the current situation, make a precise
change and know whether it has actually taken effect. Children need timely
warnings and a clear way to ask for more time and see the response.

Success means common changes and request decisions are easy to complete, their
scope is clear, and the interface accurately distinguishes intended changes from
device-confirmed results.

## Positioning

A self-hosted family-control system with one household per installation. Parents
can interact through interchangeable interfaces backed by the same application
rules. Device agents continue enforcing local policy when the central service
cannot be reached.

Google, Apple and other family-control products were named as sources of feature
and usability inspiration, not as binding visual references.

## Operating Context

The central service coordinates household accounts, desired policies and parent
commands. NixOS device agents initiate outbound connections and enforce access
through Timekpr. Devices remain authoritative for observed usage and applied
policy; the backend owns the shared allowance, allocation ledger and durable
daily usage reports alongside desired changes and delivery history.

Parents already have Telegram and Matrix interfaces, including use through an
Element client. The web interface is an additional adapter to the central API.
Children have a local application launcher, five-minute and one-minute warnings,
and a request window.

The web work follows the completed backend/interface stage. The user requested
visual mockups or prototypes for review before production web implementation.
Deployment and secret provisioning belong to the consuming Nix configuration.

## Capabilities and Constraints

- View children, access status, remaining time, requests and recent command history.
- Share a child’s allowance across devices; sum simultaneous metered usage.
- Review seven-day usage totals, partial coverage and per-device breakdowns.
- Add or deduct custom amounts of time; set total or remaining daily allowance.
- Change today's cutoff, grant an immediate session, remove today's play budget,
  or allow access for the rest of the day.
- Restore recurring defaults, block/unblock access and mute/unmute requests.
- Approve a request with a custom duration or deny it with a reason. Each request
  has a stable identity and only one resolving parent decision.
- Read and edit recurring policies with revision checks.
- Support administrative account/device approval, invitations and identity links.
- Support password and OIDC authentication. Registration and login for each method
  are independently configurable; registration requires approval by default.

Product terminology must retain meaningful distinctions:

- **Total allowance** includes time already used; **remaining time** does not.
- **Unlimited budget today** retains schedule restrictions and manual blocks.
- **Allow rest of day** opens today's schedule and budget, including homework
  periods, while retaining manual blocks.
- **Restore defaults** preserves consumed time and manual blocks.
- **Pending** is not **applied**. Offline or stale usage is unknown, not zero.

Today-only changes expire at local midnight. Commands commit centrally with durable
identities so uncertain delivery can be retried without granting time twice.
Local enforcement continues offline, but remote changes require device delivery.
The interface must communicate those limits without requiring parents to
understand the implementation.

Timekpr's scheduling/accounting limits remain real constraints. Unsupported
schedule combinations are rejected rather than silently changed. DNS profile
names currently describe desired, externally unverified state; there is no DNS
enforcement adapter.

The approved web scope includes daily controls, requests, recurring policies,
history, authentication and account/device administration. Layouts adapt to
household size and shared computers with separate operating-system accounts;
one allowance belongs to each child across their assigned computers. The selected
direction is Today’s family plan, extended with weekly usage. Holiday
schedules, weekday-specific budgets and timed pauses are backlog
ideas, not delivered features or approved web requirements.

## Evidence on Hand

- [README.md](README.md): existing capabilities, command semantics and architecture.
- [Deployment guide](docs/deployment.md): roles, authentication, approval and
  configuration workflows.
- [Implementation report](docs/review/implementation.md): verification results and
  boundaries, including simulated external messaging and OIDC services.
- [Baseline review](docs/review/2026-10-18-review.md): original findings and product
  research; historical implementation details may have been superseded.
- `pkgs/family-controls/src/family_controls/`: current application and adapters.
- `pkgs/family-controls/tests/` and `tests/`: automated workflow and integration
  evidence.

There is no incumbent parent web interface or approved visual system. Existing
test fixtures can inform demonstrations, but do not represent real customer
households, testimonials or production usage evidence.

## Product Principles

1. Make everyday exceptions precise and easy to understand.
2. Show the truth about usage, connectivity and whether a change has taken effect.
3. Preserve reliable local enforcement through connection loss and restarts.
4. Keep core behavior consistent across interfaces while respecting each one's
   interaction model.
5. Give phone and desktop users equal access to the parent workflows.
