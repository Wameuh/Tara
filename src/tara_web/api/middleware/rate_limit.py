"""Bounded, monotonic fixed-window anti-abuse limiter."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from math import ceil
from threading import Lock


class RateLimiter:
    def __init__(
        self,
        *,
        limits: dict[str, int],
        window_seconds: float,
        maximum_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not limits
            or any(not 1 <= value <= 100_000 for value in limits.values())
            or not 1 <= maximum_keys <= 100_000
            or window_seconds <= 0
        ):
            raise ValueError("rate limit is invalid")
        self.limits, self.window_seconds, self.maximum_keys, self.clock = (
            dict(limits),
            window_seconds,
            maximum_keys,
            clock,
        )
        self._items: OrderedDict[tuple[str, str], tuple[float, int]] = OrderedDict()
        self._lock = Lock()

    def allow(self, category: str, identity: str) -> int | None:
        with self._lock:
            return self._allow(category, identity)

    def _allow(self, category: str, identity: str) -> int | None:
        now = self.clock()
        category_global = f"{category}_global"
        global_category = (
            category_global if category_global in self.limits else "global"
        )
        values = []
        for key in ((global_category, "*"), (category, identity)):
            started, count = self._items.get(key, (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0
            values.append((key, started, count))
        blocked = [
            started for key, started, count in values if count >= self.limits[key[0]]
        ]
        if blocked:
            return max(1, min(60, ceil(self.window_seconds - (now - min(blocked)))))
        for key, started, count in values:
            self._items[key] = (started, count + 1)
            self._items.move_to_end(key)
        while len(self._items) > self.maximum_keys:
            self._items.popitem(last=False)
        return None
