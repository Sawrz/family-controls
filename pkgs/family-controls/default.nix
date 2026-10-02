{
  lib,
  makeWrapper,
  python3,
  sqlite,
  stdenvNoCC,
}:
let
  mkTool =
    {
      executable,
      script,
      pythonEnv ? python3,
      runtimeInputs ? [ ],
    }:
    stdenvNoCC.mkDerivation {
      pname = executable;
      version = "0.1.0";
      src = ./src;
      nativeBuildInputs = [ makeWrapper ];

      installPhase = ''
        runHook preInstall

        mkdir -p "$out/bin" "$out/share/family-controls"
        cp -r "$src"/. "$out/share/family-controls/"

        makeWrapper ${pythonEnv}/bin/python3 "$out/bin/${executable}" \
          --add-flags "$out/share/family-controls/${script}" \
          --prefix PATH : "${lib.makeBinPath runtimeInputs}"

        runHook postInstall
      '';

      meta = {
        description = "Family controls helper: ${executable}";
        license = lib.licenses.mit;
        platforms = lib.platforms.linux;
      };
    };
in
{
  "family-controlsd" = mkTool {
    executable = "family-controlsd";
    script = "family_controlsd.py";
    runtimeInputs = [ sqlite ];
  };

  "family-controls-sessiond" = mkTool {
    executable = "family-controls-sessiond";
    script = "family_controls_sessiond.py";
    pythonEnv = python3.withPackages (ps: [ ps.tkinter ]);
  };

  "family-controls-relay" = mkTool {
    executable = "family-controls-relay";
    script = "family_controls_relay.py";
  };

  "family-controlsctl" = mkTool {
    executable = "family-controlsctl";
    script = "family_controlsctl.py";
  };
}
