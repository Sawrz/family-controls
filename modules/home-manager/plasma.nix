# Import with plasma-manager when composing a Plasma desktop.
{
  lib,
  osConfig ? null,
  ...
}:
{
  config =
    lib.optionalAttrs (osConfig != null && (osConfig.services.desktopManager.plasma6.enable or false))
      {
        programs.plasma.window-rules = [
          {
            description = "Family controls countdown stays above fullscreen apps";
            match = {
              window-class = {
                value = "family-controls-countdown";
                type = "substring";
              };
            };
            apply = {
              above = {
                value = true;
                apply = "force";
              };
            };
          }
        ];
      };
}
