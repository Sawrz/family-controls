"""Composition roots for the agent and relay processes."""

import argparse
import fcntl
import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .clock import SystemClock
from .config import AgentConfig, RelayConfig, secret


def stop_event() -> threading.Event:
    event = threading.Event()

    def stop(_signal: int, _frame: Any) -> None:
        event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return event


def agent_main() -> None:
    from .adapters.cached_policy import load_cached_policy
    from .adapters.http import UnixServer
    from .adapters.logind import Logind
    from .adapters.sqlite import Repository
    from .adapters.timekpr import Timekpr, validate_config
    from .agent import AgentSync, device_client, enroll_until_approved
    from .application import Service

    parser = argparse.ArgumentParser(description="Local screen-time policy agent")
    parser.add_argument("--config", required=True)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument(
        "--restore", action="store_true", help="Restore recurring policy after the agent stops"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = AgentConfig.model_validate_json(Path(args.config).read_text())
    validate_config(config)
    if args.check_config:
        return
    clock = SystemClock(config.timezone)
    state_dir = Path(config.agent.stateDir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / "agent.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repository = Repository(str(state_dir / "state.sqlite"))
        load_cached_policy(config, repository)
        enforcer = Timekpr(config, clock)
        if args.restore:
            enforcer.restore()
            repository.close()
            return
        service = Service(config, repository, enforcer, clock, Logind())
        local = UnixServer(service)
        stop = stop_event()
        client = device_client(config)

        def synchronize() -> None:
            if enroll_until_approved(config, client, stop):
                AgentSync(service, repository, client).run(stop)

        threads = [
            threading.Thread(target=local.serve_forever, daemon=True),
            threading.Thread(target=synchronize, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(config.managedUsers))) as pool:
                while not stop.is_set():
                    list(pool.map(service.reconcile, config.managedUsers))
                    stop.wait(config.pollIntervalSeconds)
        finally:
            stop.set()
            local.shutdown()
            local.server_close()
            for thread in threads:
                thread.join(timeout=35)
            repository.close()


def relay_main() -> None:
    from .adapters.client import Gateway
    from .adapters.sqlite import Repository
    from .adapters.telegram import Relay

    parser = argparse.ArgumentParser(description="Telegram adapter for family controls")
    parser.add_argument("--config", required=True)
    parser.add_argument("--bot-token-file")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = RelayConfig.model_validate_json(Path(args.config).read_text())
    if args.check_config:
        return
    if not args.bot_token_file:
        parser.error("--bot-token-file is required")
    state_dir = Path(config.stateDir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / "relay.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repository = Repository(str(state_dir / "relay.sqlite"))
        gateway = Gateway(config.backend)
        relay = Relay(
            config,
            secret(args.bot_token_file),
            gateway,
            repository,
            SystemClock("UTC"),
        )
        stop = stop_event()

        def sync() -> None:
            from .adapters.events import consume_events

            consume_events(gateway.client, repository, relay.event, stop)

        thread = threading.Thread(target=sync, daemon=True)
        thread.start()
        try:
            relay.run(stop)
        finally:
            stop.set()
            thread.join(timeout=30)
            repository.close()
