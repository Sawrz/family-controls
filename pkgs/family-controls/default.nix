{
  lib,
  makeWrapper,
  python3,
  matrixPython ? python3,
  stdenvNoCC,
}:
let
  source = ./src/family_controls;
  core = stdenvNoCC.mkDerivation {
    pname = "family-controls-core";
    version = "0.3.0";
    src = source;
    installPhase = ''
      mkdir -p "$out/share/family_controls/backend" "$out/share/family_controls/adapters"
      for file in __init__ domain application config ports clock notifications presentation usage local_usage; do
        cp "$src/$file.py" "$out/share/family_controls/"
      done
      for file in __init__ models ports service accounts usage; do
        cp "$src/backend/$file.py" "$out/share/family_controls/backend/"
      done
      cp "$src/adapters/__init__.py" "$out/share/family_controls/adapters/"
    '';
  };
  tool =
    name: entry: modules: dependencies:
    stdenvNoCC.mkDerivation {
      pname = name;
      version = "0.3.0";
      src = source;
      nativeBuildInputs = [ makeWrapper ];
      installPhase = ''
        mkdir -p "$out/bin" "$out/share"
        cp -r ${core}/share/family_controls "$out/share/"
        chmod -R u+w "$out/share"
        ${lib.concatMapStringsSep "\n" (
          file: ''cp "$src/${file}.py" "$out/share/family_controls/${file}.py"''
        ) modules}
        makeWrapper ${
          (if name == "family-controls-matrix" then matrixPython else python3).withPackages (
            ps: [ ps.pydantic ] ++ dependencies ps
          )
        }/bin/python3 "$out/bin/${name}" \
          --set PYTHONPATH "$out/share" \
          --add-flags "-c 'from ${entry}; main()'"
      '';
      meta = {
        license = lib.licenses.mit;
        platforms = lib.platforms.linux;
      };
    };
  common = [
    "adapters/http"
    "adapters/client"
  ];
  runtime = [
    "bootstrap"
    "adapters/sqlite"
  ];
in
{
  family-controls-core = core;
  family-controls-backend =
    tool "family-controls-backend" "family_controls.backend.main import main"
      [
        "backend/main"
        "adapters/api"
        "adapters/backend_sqlite"
        "adapters/local_backend"
        "adapters/oidc"
        "adapters/passwords"
        "adapters/timekpr"
      ]
      (ps: [
        ps.fastapi
        ps.uvicorn
        ps.argon2-cffi
        ps.pyjwt
        ps.cryptography
        ps.httpx
      ]);
  family-controls-agent =
    tool "family-controls-agent" "family_controls.bootstrap import agent_main as main"
      (
        common
        ++ runtime
        ++ [
          "agent"
          "adapters/cached_policy"
          "adapters/logind"
          "adapters/timekpr"
        ]
      )
      (ps: [
        ps.httpx
        ps.dbus-python
      ]);
  family-controls-telegram =
    tool "family-controls-telegram" "family_controls.bootstrap import relay_main as main"
      (
        common
        ++ runtime
        ++ [
          "adapters/telegram"
          "adapters/events"
        ]
      )
      (ps: [ ps.httpx ]);
  family-controls-matrix =
    tool "family-controls-matrix" "family_controls.adapters.matrix import main"
      (
        common
        ++ [
          "adapters/sqlite"
          "adapters/matrix"
          "adapters/events"
        ]
      )
      (ps: [
        ps.httpx
        (ps.matrix-nio.override { withVodozemac = true; })
      ]);
  family-controlsctl = tool "family-controlsctl" "family_controls.adapters.cli import main" (
    common ++ [ "adapters/cli" ]
  ) (ps: [ ps.httpx ]);
  family-controls-desktop =
    tool "family-controls-desktop" "family_controls.adapters.countdown import main"
      [ "adapters/http" "adapters/countdown" "adapters/desktop_notifications" ]
      (ps: [
        ps.tkinter
        ps.dbus-fast
      ]);
  family-controls-render =
    tool "family-controls-render" "family_controls.adapters.timekpr import render_main as main"
      [ "adapters/timekpr" "adapters/cached_policy" "adapters/sqlite" ]
      (_ps: [ ]);
}
