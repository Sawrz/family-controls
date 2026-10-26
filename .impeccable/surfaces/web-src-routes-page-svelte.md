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
Shared machines have separate computer accounts; budgets remain per computer/account.
No child-facing web login, pooled cross-device budgets, invented analytics or DNS enforcement.
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
