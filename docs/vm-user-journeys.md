# Browser-to-device VM journeys

`checks.web-journeys` deploys two fresh NixOS guests on an uncached build:

- **Backend:** the packaged FastAPI service, HTTPS Nginx web module and Chromium.
- **Child computer:** the agent, real Timekpr/logind, two separate child accounts,
  the packaged request window, Dunst and an isolated Xvfb/Openbox desktop.

Pytest controls the browser through the Nix test driver. Parent changes use the
web interface; device assertions inspect local status, Timekpr configuration and
actual process termination. The suite does not substitute the enforcement port.

The journeys cover:

1. Parent sign-in, browser-created enrollment token and explicit device approval.
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

These are ordered stages in one disposable household. Run the whole check;
later stages deliberately retain enrollment, session and command-history state.

## Run

```sh
nix build .#checks.x86_64-linux.web-journeys --no-link --print-out-paths -L
```

The output contains `web-journeys.xml`, browser and child-window screenshots, and
both VM journals. Normal Nix caching applies. Changes to the packaged software,
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

The verified run on 2026-10-26 passed all six journeys in 231.73 seconds. Pytest
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
