# family-controls

Screen-time daemon, session countdown, Telegram relay, CLI, timekpr integration,
and NixOS/Home Manager modules. `nixosModules.default` explicitly imports sops-nix;
its existing `apiTokenSecret` and `botTokenSecret` options name runtime secrets.
Users, limits, device assignments, endpoints and secret definitions belong to the
consuming deployment. State remains under `/var/lib/family-controls` and
`/var/lib/timekpr`; extraction does not migrate or recreate it.

Import `homeManagerModules.default` for the countdown and timekpr client options.
Plasma consumers also import `homeManagerModules.plasma` alongside plasma-manager
so the countdown stays above fullscreen windows.

Run `nix flake check` for standalone package, unit and NixOS integration checks.
Development and pull requests use the private Forgejo repository; GitHub is a
private recovery mirror.

## Source access and recovery

Standalone inputs use the private Forgejo upstream mirrors. CI requires the
`NIX_FORGEJO_SSH_KEY` Actions secret with read-only access through `nix-builders`.
Public upstream recovery uses HTTPS GitHub Git, without an API token. CI runs
checks through `recovery/sources.py`; raw Nix commands do not invoke fallback.

For local checks, use your normal Forgejo Git identity:

```sh
python3 recovery/sources.py run --source . --target checks -- \
  nix flake check '{flake}' --no-write-lock-file --no-update-lock-file
```

Recovery retains the locked commit and content hash, and only handles transport
outages. Authentication and integrity errors stop the operation. Run
`python3 recovery/sources.py update-check --source .` before `nix flake update`;
updates require Forgejo. The recovery and runner-access helpers are shared
copies of the tested `nix-config` implementation and should be updated together.
