---
version: 1
slug: "web-src-routes-page-svelte"
primary_target: "web/src/routes/+page.svelte"
related_targets: ["web/src/lib","web/src/app.css"]
---

# Parent web interface

Mode: Operate. Parents and household administrators use phones and desktops equally.
Scope: daily controls, requests, recurring policies, command history, sign-in/registration,
account/device approval, invitations and identity links. English and German throughout.
SvelteKit/TypeScript static files; same-origin FastAPI cookie sessions and SSE; separate Nix package.
Shared machines have separate computer accounts; each child shares one allowance across assigned devices. Simultaneous metered usage is summed.
Weekly usage reports show observed daily totals and device breakdowns, with missing and partial coverage explicit. No child-facing web login, invented analytics or DNS enforcement.
Prototype fixtures cover three children, larger households, shared devices, offline/unknown state,
concurrent decisions, stale policy revisions, failed/expired commands and disabled auth methods.

## Direction contract

THESIS: Today's family plan puts requests and precise daily exceptions before aggregate metrics.
OWN-WORLD: Forest-green navigation, warm light ground, sage change panels, amber pending states;
readable system sans, compact labels, separated rows and plain controls. Light surfaces serve
quick daytime phone checks and longer desktop administration.
STORY: Find the child and computer, understand access, preview a change, then verify delivery.
FIRST VIEWPORT: Desktop navigation rail, day heading, request strip, child/account rows and a
right-hand change panel. On phones navigation remains accessible and the selected editor moves
above the list. Today-only markers and before/after allowance previews are the signature.
FORM: Weekly family planner, grounded candidate 1; seed:b1b4e4d4bf79a3b9. User accepted the
recommended direction and explicitly requested end-to-end implementation. Reference:
.impeccable/mocks/decision/planner.html. Code-led, no raster assets needed.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

Feedback: sending/queued/applied/failed/expired are distinct. Unknown usage is never zero.
All changes show target and duration; destructive or broad actions get explicit confirmation.
Policy conflicts retain the draft and require an explicit reload. SSE and bounded refresh
update status without replacing edits. Keyboard focus, accessible errors, reduced motion,
long German labels and narrow screens are verification targets. No open product decisions.


## Account and setup extension (approved October 2026)

The user approved end-to-end child onboarding and separate My account / Household
administration destinations. Preserve the existing forest/sage planner identity.
My account owns profile, language, enabled password management and verified
personal Telegram/Matrix links. Household sections are People, Children, Devices,
Integrations and Sign-in methods; technologies have independent visible sections,
not a technology selector. Show configured metadata, actual/stale adapter health,
linked parents and disconnect confirmation without exposing credentials.

New children show Needs setup. The inline sage setup panel confirms allowance and
allowed hours, can copy another child's schedule, and distinguishes confirmation
from device application. Persist setup centrally and retain saved offline policy.
No Nix policy seed is required. Personal links use a one-use service command plus
explicit browser confirmation of the detected identity. Keep role and ownership
checks on the backend. Desktop and phone, English and German, are equal targets.

## Chat language and request extension (approved October 2026)

Preserve the forest/sage system and existing settings controls. My account's
English/German preference also controls personal Telegram and Matrix replies;
the inheritance choice uses the household language. Integrations owns the
household default and administrator-only group/room overrides for shared
notifications. Schedules edits the child's display name without changing IDs.

Chat prioritizes Add time and Extend allowed hours, with secondary actions under
More. Telegram replaces its open menu through child selection, duration and
confirmation. Bulk confirmation names every child and the amount each receives;
Matrix provides an explicit confirmation command. Ordinary receipts stay short:
child, decision or change, amount, parent and written delivery state.

Request decisions from web, Telegram or Matrix update the original chat card and
remove stale decision controls. Waiting, applied, failed and expired remain
distinct. Resolved Telegram requests offer remaining requests or Menu without
posting an unsolicited menu. Status details remain available separately. See
`docs/chat-interfaces.md` for language precedence and transport behavior.
