"""Small latest-bars cache shared by chart refresh and submit-analysis flow."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from pa_agent.data.base import KlineBar


@dataclass(frozen=True, slots=True)
class LatestBarsCache:
    raw_input: str
    resolved_symbol: str
    market: str
    asset_type: str
    timeframe: str
    bar_count: int
    bars: tuple[KlineBar, ...]
    provider: str
    fetched_at: datetime
    latest_bar_time: datetime | None
    warning: str | None = None

    def has_enough_bars(self, min_bars: int = 50) -> bool:
        closed_count = sum(1 for bar in self.bars if bar.closed)
        return closed_count >= min_bars


class MarketDataCache:
    """Keeps the newest successful K-line result for analysis fallback."""

    def __init__(self) -> None:
        self._latest: LatestBarsCache | None = None

    def update(
        self,
        *,
        raw_input: str,
        resolved_symbol: str,
        market: str,
        asset_type: str,
        timeframe: str,
        requested_bar_count: int,
        bars: Sequence[KlineBar],
        provider: str,
        warning: str | None = None,
    ) -> LatestBarsCache:
        latest = _latest_bar_time(bars)
        entry = LatestBarsCache(
            raw_input=raw_input,
            resolved_symbol=resolved_symbol,
            market=market,
            asset_type=asset_type,
            timeframe=timeframe,
            bar_count=int(requested_bar_count),
            bars=tuple(bars),
            provider=provider,
            fetched_at=datetime.now(),
            latest_bar_time=latest,
            warning=warning,
        )
        self._latest = entry
        return entry

    def get_latest(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> LatestBarsCache | None:
        entry = self._latest
        if entry is None:
            return None
        if timeframe is not None and entry.timeframe != timeframe:
            return None
        if symbol is not None and symbol not in {
            entry.raw_input,
            entry.resolved_symbol,
        }:
            return None
        return entry

    def clear(self) -> None:
        self._latest = None


def _latest_bar_time(bars: Sequence[KlineBar]) -> datetime | None:
    if not bars:
        return None
    ts = float(bars[0].ts_open)
    if abs(ts) > 10_000_000_000:
        ts /= 1000.0
    return datetime.fromtimestamp(ts)


__all__ = ["LatestBarsCache", "MarketDataCache"]
