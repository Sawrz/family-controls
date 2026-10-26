"""System clock shared by composition roots without importing optional adapters."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    def __init__(self, timezone: str) -> None:
        self.timezone = ZoneInfo(timezone)

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def monotonic(self) -> float:
        return time.monotonic()
