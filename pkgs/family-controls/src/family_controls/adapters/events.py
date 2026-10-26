"""Reconnectable interface delivery with independent durable consumer progress."""

import logging
import threading
from collections.abc import Callable

from ..domain import InvalidCommand, Unavailable
from .client import BackendClient
from .sqlite import Repository

LOG = logging.getLogger(__name__)


def consume_events(
    client: BackendClient,
    repository: Repository,
    deliver: Callable[[dict], None],
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        saved = repository.relay_get("event-cursor", {"id": 0})
        cursor = saved["id"] if isinstance(saved, dict) else 0
        try:
            for event in client.events("/api/v1/events", cursor):
                if stop.is_set():
                    return
                if event is not None:
                    deliver(event)
                    repository.relay_set("event-cursor", {"id": event["id"]})
        except (Unavailable, InvalidCommand, OSError):
            LOG.warning("Notification delivery interrupted; cursor retained for replay")
            stop.wait(3)
