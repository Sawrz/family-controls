"""Composition roots for the agent and relay processes."""

import argparse
import fcntl
import logging
import signal
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .adapters.http import Gateway, TCPServer, UnixServer
from .adapters.logind import Logind
from .adapters.sqlite import Repository
from .adapters.telegram import Relay
from .adapters.timekpr import Timekpr, validate_config
from .application import Service
from .config import AgentConfig, RelayConfig, secret


class SystemClock:
    def __init__(self, timezone: str) -> None:
        self.timezone = ZoneInfo(timezone)

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(self.timezone)


def stop_event() -> threading.Event:
    event = threading.Event()

    def stop(_signal: int, _frame: Any) -> None:
        event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return event


def agent_main() -> None:
    parser = argparse.ArgumentParser(description="Local screen-time policy agent")
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file")
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
    enforcer = Timekpr(config, clock)
    if args.restore:
        enforcer.restore()
        return
    if not args.token_file:
        parser.error("--token-file is required")
    state_dir = Path(config.agent.stateDir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    with (state_dir / "agent.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repository = Repository(str(state_dir / "state.sqlite"))
        service = Service(config, repository, enforcer, clock, Logind())
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(config.agent.tlsCertPath, config.agent.tlsKeyPath)
        tcp = TCPServer(
            (config.agent.bindHost, config.agent.port), service, secret(args.token_file), tls
        )
        local = UnixServer(service)
        stop = stop_event()
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True) for server in (tcp, local)
        ]
        for thread in threads:
            thread.start()
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(config.managedUsers))) as pool:
                pruned_day = None
                while not stop.is_set():
                    list(pool.map(service.reconcile, config.managedUsers))
                    if pruned_day != clock.now().date():
                        repository.prune(clock.now(), config.auditRetentionDays)
                        pruned_day = clock.now().date()
                    stop.wait(config.pollIntervalSeconds)
        finally:
            for server in (tcp, local):
                server.shutdown()
                server.server_close()
            repository.close()


def relay_main() -> None:
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
        relay = Relay(
            config, secret(args.bot_token_file), Gateway(config), repository, SystemClock("UTC")
        )
        stop = stop_event()

        def sync() -> None:
            while not stop.is_set():
                relay.sync_requests()
                stop.wait(config.pollIntervalSeconds)

        thread = threading.Thread(target=sync, daemon=True)
        thread.start()
        try:
            relay.run(stop)
        finally:
            stop.set()
            thread.join(timeout=30)
            repository.close()
