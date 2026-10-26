"""Central service composition root."""

import argparse
import fcntl
from pathlib import Path

import uvicorn

from ..adapters.api import create_app
from ..adapters.backend_sqlite import BackendStore
from ..adapters.oidc import OIDC
from ..adapters.passwords import ArgonPasswords
from ..adapters.timekpr import validate_user
from ..clock import SystemClock
from .accounts import Accounts
from .models import BackendConfig
from .service import Backend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    config = BackendConfig.model_validate_json(Path(args.config).read_text())
    for child in config.children.values():
        validate_user(child.policy)
    if args.check_config:
        return
    directory = Path(config.stateDir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "backend.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store, clock = BackendStore(str(directory / "backend.sqlite")), SystemClock("UTC")
        try:
            backend = Backend(config, store, clock, validate_user)
            accounts = Accounts(config.authentication, store, ArgonPasswords(), clock)
            app = create_app(backend, accounts, OIDC(config, store, clock, accounts))
            uvicorn.run(
                app,
                host=config.bindHost,
                port=config.port,
                workers=1,
                ssl_certfile=config.tlsCertFile,
                ssl_keyfile=config.tlsKeyFile,
                proxy_headers=False,
                access_log=False,
                timeout_keep_alive=10,
                limit_concurrency=128,
                timeout_graceful_shutdown=25,
            )
        finally:
            store.close()
