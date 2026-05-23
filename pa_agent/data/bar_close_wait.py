"""Helpers for waiting until the current forming bar closes."""
from __future__ import annotations

import math
import re
import time

from pa_agent.data.base import KlineBar

_TIMEFRAME_SECONDS_RE = re.compile(r"^(\d+)([mhdw])$", re.IGNORECASE)

# Month uses uppercase M in MT5; UI combos use lowercase units only.
_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}


def timeframe_to_seconds(timeframe: str) -> int | None:
    """Map timeframe string (e.g. ``5m``, ``1h``) to bar duration in seconds."""
    tf = str(timeframe or "").strip()
    if not tf:
        return None
    if tf == "1M":
        return None
    if tf in _TIMEFRAME_SECONDS:
        return _TIMEFRAME_SECONDS[tf]
    m = _TIMEFRAME_SECONDS_RE.match(tf)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    if unit == "w":
        return n * 7 * 86400
    return None


def seconds_until_bar_closes(
    ts_open_ms: int,
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> int | None:
    """Whole seconds until the bar that opened at ``ts_open_ms`` closes."""
    duration_s = timeframe_to_seconds(timeframe)
    if duration_s is None:
        return None
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    close_ms = int(ts_open_ms) + duration_s * 1000
    remaining_ms = close_ms - int(now_ms)
    if remaining_ms <= 0:
        return 0
    return int(math.ceil(remaining_ms / 1000))


def current_forming_ts(bars_newest_first: list[KlineBar]) -> int | None:
    """Return ts_open of the newest (forming) bar, or None if empty."""
    if not bars_newest_first:
        return None
    if bars_newest_first[0].closed:
        return None
    return int(bars_newest_first[0].ts_open)


def forming_bar_has_closed(
    waited_ts_open: int,
    bars_newest_first: list[KlineBar],
) -> bool:
    """True when a new forming bar replaced the one we were waiting on."""
    if not bars_newest_first:
        return False
    if bars_newest_first[0].closed:
        return True
    return int(bars_newest_first[0].ts_open) != waited_ts_open
