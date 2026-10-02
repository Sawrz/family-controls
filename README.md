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
