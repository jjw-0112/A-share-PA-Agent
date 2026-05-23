"""Thread-safe ring buffer for K-line bars."""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional

from pa_agent.data.base import KlineBar


class KlineBuffer:
    """Thread-safe storage for K-line bars.

    Internal layout:
    - _closed: deque of closed bars, newest-first, capped at *capacity*.
    - _forming: the single currently-forming (unclosed) bar, or None.

    All public methods acquire _lock before touching internal state.
    """

    def __init__(self, capacity: int = 500) -> None:
        if capacity < 2:
            raise ValueError("capacity must be >= 2")
        self._capacity = capacity
        self._closed: deque[KlineBar] = deque()
        self._forming: Optional[KlineBar] = None
        self._lock = threading.RLock()

    # ── Write ─────────────────────────────────────────────────────────────────

    def append(self, bar: KlineBar) -> None:
        """Append a newly-closed bar.  Trims oldest bars beyond capacity."""
        with self._lock:
            # Promote the previous forming bar to closed if it matches
            if self._forming is not None and self._forming.ts_open == bar.ts_open:
                # The forming bar just closed — replace it
                self._forming = None
            if self._closed and self._closed[0].ts_open == bar.ts_open:
                self._closed[0] = bar
                return
            # Insert at front (newest-first)
            self._closed.appendleft(bar)
            # Trim to capacity
            while len(self._closed) > self._capacity:
                self._closed.pop()

    def update_forming(self, bar: KlineBar) -> None:
        """Replace the current forming bar (tick update)."""
        with self._lock:
            self._forming = bar

    def clear(self) -> None:
        """Remove all bars (called on symbol/timeframe switch)."""
        with self._lock:
            self._closed.clear()
            self._forming = None

    # ── Read ──────────────────────────────────────────────────────────────────

    def last_n_including_forming(self, n: int) -> list[KlineBar]:
        """Return up to *n* bars newest-first, with the forming bar at index 0.

        If the forming bar is absent, index 0 is the newest closed bar.
        Returns fewer than *n* bars if the buffer has fewer.
        """
        with self._lock:
            result: list[KlineBar] = []
            if self._forming is not None:
                result.append(self._forming)
            for bar in self._closed:
                if len(result) >= n:
                    break
                result.append(bar)
            return result

    def snapshot_view(self) -> list[KlineBar]:
        """Return all bars newest-first (forming + all closed)."""
        with self._lock:
            result: list[KlineBar] = []
            if self._forming is not None:
                result.append(self._forming)
            result.extend(self._closed)
            return result

    @property
    def size(self) -> int:
        """Total number of bars (forming + closed)."""
        with self._lock:
            return len(self._closed) + (1 if self._forming is not None else 0)
