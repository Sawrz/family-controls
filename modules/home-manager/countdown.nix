{
  config,
  lib,
  familyControlsPackages,
  ...
}:
let
  cfg = config.custom.configs.familyControlsCountdown;
in
{
  options.custom.configs.familyControlsCountdown = {
    enable = lib.mkEnableOption "family controls countdown helper";
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ familyControlsPackages."family-controls-sessiond" ];

    systemd.user.services.family-controls-sessiond = {
      Unit = {
        Description = "Family controls countdown helper";
        PartOf = [ "graphical-session.target" ];
        After = [ "graphical-session.target" ];
        StartLimitIntervalSec = 60;
        StartLimitBurst = 5;
      };

      Service = {
        ExecStart = "${familyControlsPackages."family-controls-sessiond"}/bin/family-controls-sessiond";
        Restart = "on-failure";
        RestartSec = 10;
      };

      Install = {
        WantedBy = [ "graphical-session.target" ];
      };
    };
  };
}
