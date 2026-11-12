"""Per-host request spacing.

Four stages per URL against the same publisher is exactly the pattern that gets
an IP scored, and a scored IP turns into 403s and 429s. A small delay between
requests to the same host keeps the tool a polite client — cheaper than any
proxy, and it makes the failure modes rarer rather than louder.
"""

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import urlparse

MIN_INTERVAL = float(os.getenv("OFFPRINT_HOST_INTERVAL", "1.2"))


class HostLimiter:
    def __init__(self, min_interval: float = MIN_INTERVAL) -> None:
        self.min_interval = min_interval
        self._next_allowed: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, url: str) -> float:
        """Sleep until this host may be contacted again. Returns seconds slept."""
        host = (urlparse(url).hostname or "").lower()
        if not host or self.min_interval <= 0:
            return 0.0

        async with self._lock:
            now = time.monotonic()
            ready_at = self._next_allowed.get(host, 0.0)
            self._next_allowed[host] = max(now, ready_at) + self.min_interval
            delay = ready_at - now

        if delay > 0:
            await asyncio.sleep(delay)
            return delay
        return 0.0

    def forget(self, url: str) -> None:
        self._next_allowed.pop((urlparse(url).hostname or "").lower(), None)
