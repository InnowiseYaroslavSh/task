from collections import defaultdict
from time import monotonic

from app.core.errors import RateLimitedError


class InMemoryRateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._events: defaultdict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = monotonic()
        events = [timestamp for timestamp in self._events[key] if timestamp > now - 60]
        if len(events) >= self.limit:
            raise RateLimitedError("Too many requests")
        events.append(now)
        self._events[key] = events
