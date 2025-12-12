"""In-memory buffering of write points for temporary InfluxDB outages."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class SensorBuffer:
    """FIFO buffer for a single sensor's points."""

    max_age: timedelta = timedelta(hours=1)
    max_points: int = 5000
    _items: deque[tuple[datetime, Any]] = field(default_factory=deque)

    def enqueue(self, value: Any, timestamp: datetime) -> None:
        """Append a point, pruning old items and trimming size."""
        self.prune(timestamp)
        self._items.append((timestamp, value))
        while len(self._items) > self.max_points:
            self._items.popleft()

    def has_items(self) -> bool:
        """Return True if buffer is non-empty."""
        return bool(self._items)

    def prune(self, now: datetime) -> None:
        """Drop items older than max_age."""
        cutoff = now - self.max_age
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()

    async def flush(
        self,
        write_fn: Callable[[datetime, Any], Awaitable[None]],
        now: datetime,
        error_types: tuple[type[Exception], ...],
    ) -> bool:
        """
        Write buffered items oldest-first.

        Returns True if fully flushed (or empty), False on first failure.
        """
        if not self._items:
            return True

        self.prune(now)
        while self._items:
            ts, val = self._items[0]
            try:
                await write_fn(ts, val)
            except error_types:
                return False
            self._items.popleft()

        return True
