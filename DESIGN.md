---
name: "Family Controls"
description: "A grounded family planner with clear account scope and visible change outcomes."
colors:
  green: "#254e3d"
  ground: "#faf8ef"
  sage: "#dce8d9"
  ink: "#23392e"
  muted: "#536154"
  line: "#ccd4c6"
  amber: "#795216"
  error: "#992e26"
  focus: "#936513"
  white: "#fff"
  primary-hover: "#183d2c"
  control-hover: "#e8eddf"
  control-border: "#82927e"
  field-ground: "#fffef9"
  field-border: "#7d8a78"
  field-disabled: "#e9ede1"
  editor-ground: "#eef2e7"
  request-ground: "#efe6d1"
  warning-ground: "#eee3c8"
  warning-ink: "#6c4c18"
  failure-ground: "#f9e7e1"
  failure-ink: "#80271f"
  success-ground: "#e4eddd"
  success-ink: "#263f26"
  badge-ground: "#dfeadc"
  rail-hover: "#3a614d"
  rail-ink: "#e4edde"
  progress-ground: "#e0e5d9"
  progress-fill: "#527653"
typography:
  headline:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "2.125rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.025em"
  title:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1.35rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  section:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1.1rem"
    fontWeight: 700
    lineHeight: 1.35
  body:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "0.9rem"
    fontWeight: 600
    lineHeight: 1.5
  help:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  time:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1.65rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.025em"
rounded:
  field: "5px"
  button: "6px"
  panel: "12px"
  count: "20px"
  avatar: "50%"
spacing:
  compact: "0.5rem"
  field: "0.75rem"
  standard: "1rem"
  panel: "1.5rem"
  section: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.white}"
    rounded: "{rounded.button}"
    padding: "0.65rem 1rem"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.button}"
    padding: "0.65rem 1rem"
  button-secondary-hover:
    backgroundColor: "{colors.control-hover}"
  button-danger:
    backgroundColor: "{colors.error}"
    textColor: "{colors.white}"
    rounded: "{rounded.button}"
    padding: "0.65rem 1rem"
  button-text:
    backgroundColor: "transparent"
    textColor: "{colors.green}"
    rounded: "{rounded.button}"
    padding: "0.4rem 0.6rem"
  input:
    backgroundColor: "{colors.field-ground}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "0.7rem 0.75rem"
  navigation-active:
    backgroundColor: "{colors.sage}"
    textColor: "{colors.green}"
    rounded: "{rounded.button}"
    padding: "0.85rem"
  badge-warning:
    backgroundColor: "{colors.warning-ground}"
    textColor: "{colors.warning-ink}"
    rounded: "{rounded.field}"
    padding: "0.3rem 0.65rem"
  change-panel:
    backgroundColor: "{colors.editor-ground}"
    rounded: "{rounded.panel}"
    padding: "1.5rem"
  request-banner:
    backgroundColor: "{colors.request-ground}"
    rounded: "{rounded.panel}"
    padding: "1.25rem 1.5rem"
  family-row:
    padding: "1.5rem 0"
  alert-error:
    backgroundColor: "{colors.failure-ground}"
    textColor: "{colors.failure-ink}"
    rounded: "{rounded.panel}"
    padding: "1rem 1.25rem"
---

# Design System: Family Controls

## Overview

**Creative North Star: "Today’s family plan"**

The family planner uses forest navigation, a warm cream ground and quiet sage work areas to make account changes easy to read. Compact labels, separated rows and plain controls support both quick phone checks and longer desktop administration.

The visual system keeps the child, computer and operating-system account together. Color reinforces written status; allowance previews distinguish the total budget from what remains. English and German use the same layout, with wrapping instead of truncated labels.

**Key Characteristics:**

- Forest navigation and warm, flat working surfaces.
- Separated account rows with tabular time figures.
- Scope-aware change headings and before/after allowance previews.
- Equal phone and desktop access with visible keyboard focus.

This record is extracted from `web/src/app.css`, `web/src/routes/+page.svelte`
and the components in `web/src/lib/components/`. The direction contract lives in
`.impeccable/surfaces/web-src-routes-page-svelte.md`; the decision sketch is a
critique reference, not a token source. No raster assets ship.

## Colors

Muted greens, warm cream and amber give work, selection and waiting distinct roles.
The frontmatter owns the exact values.

### Primary

- **Forest (`green`)** anchors navigation, primary actions and links.
- **Deep forest (`primary-hover`)** darkens primary button hover.
- **Rail green (`rail-hover`)** highlights navigation hover against the forest rail.

### Secondary

- **Sage (`sage`)** marks active navigation, selected minute presets and initial avatars.
- **Pale sage (`editor-ground`)** groups the change editor; it is lighter than selection sage.
- **Progress green (`progress-fill`)** fills usage bars over `progress-ground`.

### Tertiary

- **Amber (`amber`)** names waiting states. `request-ground` holds incoming requests;
  `warning-ground` and `warning-ink` identify warnings and pending badges.
- **Error red (`error`)** identifies destructive actions. `failure-ground` and
  `failure-ink` provide the readable error-message pair.
- **Success green (`success-ground`, `success-ink`)** supports confirmation alerts.

### Neutral

- **Warm cream (`ground`)** is the page ground; `field-ground` is the lighter input surface.
- **Forest ink (`ink`)** carries primary text; `muted` carries help text.
- **Soft divider (`line`)** separates account, history and administration rows.
- **Control borders (`control-border`, `field-border`)** define actionable boundaries.
- **Focus amber (`focus`)** provides a visible keyboard outline.

**The Written State Rule.** Pair state color with a readable status; a pending change must not appear applied.

## Typography

The implementation uses the system sans stack recorded in the frontmatter for
body text and working headings. This reflects the approved readable system-sans
direction; it does not establish a separate expressive display-font role.

The hierarchy is functional: page headline, section title, row heading, body,
label and help. Strong time figures use tabular numerals. Page headlines reduce
to (1.8rem) at the phone breakpoint; time figures reduce to (1.5rem) at the compact
desktop breakpoint. Body paragraphs stop at (75ch). Labels use sentence case;
long headings and paragraphs can wrap anywhere.

## Layout

Desktop uses a sticky forest rail (218px) alongside a fluid workspace capped at
(1700px). Workspace padding is (2.5rem) vertically and
`clamp(1.2rem, 3vw, 3.5rem)` horizontally. Account rows sit on the page ground,
separated by thin rules rather than independent cards. The recurring spacing
steps are recorded in the frontmatter; component-specific values remain local.

When selected, the change editor occupies a (300–340px) column beside the list.
At (1120px) and below, the rail narrows to (185px), the editor uses (300px), and
headers and time rows wrap. At (820px) and below, navigation becomes a wrapping
top band, workspace padding becomes (1.75rem 1.1rem), and the editor moves above
the account list. Filters stack. Schedule and administration grids collapse
from two columns to one. No workflow disappears on phones.

Authentication uses a two-column layout capped at (1050px), with a forest
introduction panel. On phones it becomes one column capped at (500px).

## Elevation & Depth

Depth comes from background tones and (1px) rules. Panels, controls and navigation
have no shadows or backdrop blur.

**The Flat Surface Rule.** Use tonal surfaces and thin dividers to group work; the shipped system has no shadows.

## Shapes

Controls have modest corners: field and badge corners use `rounded.field`,
buttons use `rounded.button`, and panels use `rounded.panel`. Small request
counts are pill-shaped; initial avatars are circles. Progress bars are slim
(6px) tracks with (4px) corners. Rows stay open on the page ground.

## Components

### Buttons

Primary actions use forest with white text; secondary actions use a transparent
surface with a visible muted border. Danger actions use error red. Text actions
are underlined and retain button semantics. Default controls have a minimum
height of (44px), while the editor’s compact Close action is a local exception.
Disabled buttons use opacity (0.6) and a not-allowed cursor.

Keyboard focus uses a (3px) amber outline with a (3px) offset. Buttons and links
transition background color over (0.15s ease-out) only when reduced motion is not
requested. Danger buttons retain their red surface on hover.

### Inputs / Fields

Fields use the light input ground, a visible border and the field radius, with a
minimum height of (44px). Labels sit above controls; help and error messages stay
in the form flow. Disabled text inputs use `field-disabled`. Checkboxes remain
native, with forest accent and a (20px) square. The three minute presets show
selection with sage fill and a forest border.

### Navigation

The forest rail holds plain text buttons. The current page uses sage with forest
text and stronger weight; hover uses rail green. Request counts are small sage
pills. On phones, navigation wraps above the account and language controls.

### Status badges and alerts

Badges use the field radius, compact padding and wrapping labels. Pending states
use warning amber, failure states use the failure pair, and neutral completion
badges use `badge-ground`. Alerts use the panel radius and remain in document
flow. They name the outcome rather than relying on color alone.

### Request banner and account rows

Incoming requests occupy a warm amber panel with the child, reason, computer and
review action. Account rows retain identity, computer/account metadata, access
state and remaining time. Usage bars appear only when reliable finite usage is
available. Offline and unknown states keep readable labels and last-seen context.

### Change editor

The pale sage editor names its target and scope. Budget changes preview total
allowance and remaining time separately, accounting for already consumed time
when known. Uncertain values remain unknown, with explanatory copy. Today-only,
multi-day and persistent actions use appropriate headings. Broad actions expose
an explicit confirmation checkbox before their primary action becomes available.
The panel’s heading receives focus when opened, and the preview remains in the
form flow on phones.

## Do's and Don'ts

### Do:

- Do keep child, computer and account labels together.
- Do show total allowance and remaining time as separate before/after values.
- Do let English and German labels wrap and keep phone controls available.
- Do use written unknown, pending and failure states alongside their color.

### Don't:

- Don’t imply that an accepted or queued command is already applied.
- Don’t present unknown usage as zero or draw a usage bar without reliable values.
- Don’t label persistent or multi-day changes as today-only.
