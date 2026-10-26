"""Local warning thresholds independent of network delivery or UI toolkit."""


class Warnings:
    def __init__(self) -> None:
        self.day: str | None = None
        self.fired: set[int] = set()

    def observe(self, status: dict) -> int | None:
        day = status.get("overrideExpiresAt")
        if day != self.day:
            self.day, self.fired = day, set()
        remaining = status.get("remainingCountdownSeconds")
        if remaining is None or status.get("phase") == "unknown":
            return None
        for threshold in (300, 60):
            if remaining > threshold:
                self.fired.discard(threshold)
        crossed = [
            threshold
            for threshold in (300, 60)
            if 0 < remaining <= threshold and threshold not in self.fired
        ]
        if not crossed:
            return None
        self.fired.update(crossed)
        return min(crossed)
