"""Tests for latest chart bar cache used by submit-analysis fallback."""
from __future__ import annotations

from pa_agent.data.base import KlineBar
from pa_agent.data.market_data_cache import MarketDataCache


def _bar(seq: int, closed: bool = True) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=1_717_000_000_000 + seq * 60_000,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=1000,
        closed=closed,
    )


def test_cache_filters_by_symbol_and_timeframe() -> None:
    cache = MarketDataCache()
    cache.update(
        raw_input="000006",
        resolved_symbol="STOCK:000006",
        market="CN",
        asset_type="stock",
        timeframe="1d",
        requested_bar_count=50,
        bars=[_bar(i) for i in range(60)],
        provider="sina-stock-daily",
    )

    assert cache.get_latest(symbol="000006", timeframe="1d") is not None
    assert cache.get_latest(symbol="STOCK:000006", timeframe="1d") is not None
    assert cache.get_latest(symbol="000005", timeframe="1d") is None
    assert cache.get_latest(symbol="000006", timeframe="1m") is None


def test_cache_requires_enough_closed_bars() -> None:
    cache = MarketDataCache()
    entry = cache.update(
        raw_input="600519",
        resolved_symbol="STOCK:600519",
        market="CN",
        asset_type="stock",
        timeframe="1d",
        requested_bar_count=50,
        bars=[_bar(1, closed=False), *[_bar(i) for i in range(2, 51)]],
        provider="eastmoney-stock-daily",
    )

    assert entry.has_enough_bars(min_bars=50) is False
    assert entry.has_enough_bars(min_bars=49) is True
