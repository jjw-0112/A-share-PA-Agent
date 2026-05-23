"""Market data request model shared by UI, data sources, and cache."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MarketDataMode(str, Enum):
    LATEST = "latest"
    RANGE = "range"


@dataclass(frozen=True, slots=True)
class MarketDataRequest:
    mode: MarketDataMode
    symbol: str
    timeframe: str
    bar_count: int
    start_at: datetime | None = None
    end_at: datetime | None = None

    @property
    def is_range(self) -> bool:
        return self.mode == MarketDataMode.RANGE


__all__ = ["MarketDataMode", "MarketDataRequest"]
