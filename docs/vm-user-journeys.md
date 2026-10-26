# Browser-to-device VM journeys

`checks.web-journeys` deploys three fresh NixOS guests on an uncached build:

- **Backend:** FastAPI, the HTTP web listener, an external HTTPS proxy and Chromium.
- **Child computer:** the agent, real Timekpr/logind, two separate child accounts,
  the packaged request window, Dunst and an isolated Xvfb/Openbox desktop.
- **Laptop:** a second real agent/Timekpr/logind host assigned to the same child.

Pytest controls the browser through the Nix test driver. Parent changes use the
web interface; device assertions inspect local status, Timekpr configuration and
actual process termination. The suite does not substitute the enforcement port.

The journeys cover:

1. Parent sign-in through an external HTTPS proxy and the module’s HTTP listener;
   two declarative devices connect and enroll without invitations or approval.
2. A custom grant applied to today's Timekpr allowance, a lost HTTP response,
   reload and safe retry without granting twice or changing a sibling's budget.
3. Five-minute and one-minute native notifications, the notification's request
   action, keyboard submission in the child window, and parent approval in the web UI.
4. Exhaustion stopping a running activity, an extension allowing another session,
   removal of today's play budget, manual blocking and restoration.
5. Loss of the child VM's network connection, a queued browser grant, backend
   restart, and a single application after reconnection.
6. A saved routine reaching the device and surviving an agent restart, with a
   narrow German browser layout captured afterward.
7. Simultaneous activity on both computers, additive usage and weekly device
   breakdowns in English desktop and German mobile layouts.
8. Both computers disconnected during metered use, exhausting their reserved
   credit locally and reconciling usage after reconnection.
9. Credential rotation and removal reject old/cross-device tokens and preserve
   weekly usage through backend restarts.

These are ordered stages in one disposable household. Run the whole check;
later stages deliberately retain enrollment, session and command-history state.

## Run

```sh
nix build .#checks.x86_64-linux.web-journeys --no-link --print-out-paths -L
```

The output contains `web-journeys.xml`, browser and child-window screenshots, and
all three VM journals. Normal Nix caching applies. Changes to the packaged software,
VM configuration or journey tests invalidate the check.

For a fresh boot even when the check is cached, run the driver directly:

```sh
journey_driver=$(nix build .#checks.x86_64-linux.web-journeys.driver --no-link --print-out-paths)
journey_run=$(mktemp -d)
(cd "$journey_run" && "$journey_driver/bin/nixos-test-driver" -o "$journey_run")
```

The test requires hardware virtualization and about 3.5 GiB of guest RAM.
Timeouts and assertions fail the check; neither failed journeys nor warnings are
silently converted into skips. Credentials and certificates are disposable test
fixtures. The browser accepts the fixture's self-signed certificate; agent and
Nginx upstream certificate verification remain enabled.

The verified run on 2026-10-26 passed all nine journeys in 263.28 seconds. Pytest
treats warnings as errors. After pytest completes, the pinned Nix test driver can
emit resource-cleanup warnings during its own teardown; the successful run exited
with status zero. No dependency code was modified or warnings suppressed.

## Boundaries

This verifies the connected parent/child workflow on the test's X11 desktop.
It does not prove Plasma/Wayland fullscreen placement, physical suspend, or
interoperability with live Telegram, Matrix or OIDC providers. The existing
`web-browser` check retains broader form, authentication and accessibility coverage;
`integration` covers additional device crash, midnight and chat-adapter scenarios.

Sources: [VM deployment](../tests/web-vm.nix) and
[pytest journeys](../tests/web_vm/test_journeys.py).

The final run’s artifacts are in
`/nix/store/c0w2lilr80v26v3vyngbxk9di0yn41gr-vm-test-run-family-controls-web-journeys`.
`request-approved.png` captures the child’s usage and shared balance; the final
child capture is blank after the later session-termination journey. Browser
reports, JUnit results and three journals remain available in that output.

The suite also rotates and removes a declarative credential through backend
configuration/restarts, rejects old and cross-device tokens, and checks that
weekly usage survives. The separate central VM suite retains manual enrollment
and messaging-adapter workflows. Browser checks exercise OIDC callback routing
through both proxy hops with a controlled provider; no live household IdP is used.
