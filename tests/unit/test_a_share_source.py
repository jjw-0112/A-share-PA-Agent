"""Unit tests for the AKShare A-share data source."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from pa_agent.data.a_share import AShareSource, parse_a_share_symbol
from pa_agent.data.base import DataSourceTransientError


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "时间": ["2026-05-22 09:30:00", "2026-05-22 09:31:00"],
            "开盘": [10.0, 10.2],
            "最高": [10.3, 10.5],
            "最低": [9.9, 10.1],
            "收盘": [10.2, 10.4],
            "成交量": [1000, 1200],
        }
    )


def _connected_source(monkeypatch: pytest.MonkeyPatch, fake_ak: object) -> AShareSource:
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    src = AShareSource()
    src.connect()
    return src


def test_parse_symbol_classifies_stock_etf_index() -> None:
    assert parse_a_share_symbol("600519").display == "STOCK:600519"
    assert parse_a_share_symbol("ETF:510300").display == "ETF:510300"
    assert parse_a_share_symbol("INDEX:sh000001").display == "INDEX:sh000001"
    assert parse_a_share_symbol("sz399006").display == "INDEX:sz399006"


def test_stock_minute_fetch_normalizes_and_maps_1h(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def stock_zh_a_hist_min_em(**kwargs):
        calls.append(kwargs)
        return _sample_df()

    src = _connected_source(
        monkeypatch,
        SimpleNamespace(stock_zh_a_hist_min_em=stock_zh_a_hist_min_em),
    )
    src.subscribe("600519", "1h")
    bars = src.latest_snapshot(2)

    assert calls[0]["symbol"] == "600519"
    assert calls[0]["period"] == "60"
    assert len(bars) == 2
    assert bars[0].seq == 1
    assert bars[0].close == pytest.approx(10.4)


def test_etf_and_index_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fund_etf_hist_min_em(**kwargs):
        called.append(f"etf:{kwargs['symbol']}:{kwargs['period']}")
        return _sample_df()

    def index_zh_a_hist_min_em(**kwargs):
        called.append(f"index:{kwargs['symbol']}:{kwargs['period']}")
        return _sample_df()

    fake = SimpleNamespace(
        fund_etf_hist_min_em=fund_etf_hist_min_em,
        index_zh_a_hist_min_em=index_zh_a_hist_min_em,
    )
    src = _connected_source(monkeypatch, fake)
    src.subscribe("ETF:510300", "5m")
    assert src.latest_snapshot(1)[0].close == pytest.approx(10.4)
    src.subscribe("INDEX:sh000001", "15m")
    assert src.latest_snapshot(1)[0].close == pytest.approx(10.4)
    assert called == ["etf:510300:5", "index:000001:15"]


def test_cache_hits_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    count = {"n": 0}

    def stock_zh_a_hist_min_em(**kwargs):
        del kwargs
        count["n"] += 1
        return _sample_df()

    src = _connected_source(
        monkeypatch,
        SimpleNamespace(stock_zh_a_hist_min_em=stock_zh_a_hist_min_em),
    )
    src.subscribe("STOCK:600519", "1m")
    src.latest_snapshot(2)
    src.latest_snapshot(2)
    assert count["n"] == 1


def test_empty_data_and_endpoint_errors_are_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def empty(**kwargs):
        del kwargs
        return pd.DataFrame()

    src = _connected_source(monkeypatch, SimpleNamespace(stock_zh_a_hist_min_em=empty))
    src.subscribe("600519", "1m")
    with pytest.raises(DataSourceTransientError):
        src.latest_snapshot(2)


def test_daily_fallback_uses_sina_when_eastmoney_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def eastmoney(**kwargs):
        del kwargs
        called.append("eastmoney")
        raise RuntimeError("proxy down")

    def sina(**kwargs):
        called.append(f"sina:{kwargs['symbol']}")
        return pd.DataFrame(
            {
                "date": ["2026-05-21", "2026-05-22"],
                "open": [10.0, 10.2],
                "high": [10.5, 10.7],
                "low": [9.8, 10.1],
                "close": [10.2, 10.6],
                "volume": [1000, 1100],
            }
        )

    src = _connected_source(
        monkeypatch,
        SimpleNamespace(stock_zh_a_hist=eastmoney, stock_zh_a_daily=sina),
    )
    src.subscribe("000006", "1d")
    bars = src.latest_snapshot(2)

    assert called == ["eastmoney", "sina:sz000006"]
    assert len(bars) == 2
    assert bars[0].close == pytest.approx(10.6)
    assert src.last_provider == "sina-stock-daily"
    assert src.last_warning and "eastmoney-stock-daily" in src.last_warning


def test_hk_stock_daily_route(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def stock_hk_hist(**kwargs):
        called.append(kwargs["symbol"])
        return pd.DataFrame(
            {
                "日期": ["2026-05-21", "2026-05-22"],
                "开盘": [300.0, 301.0],
                "最高": [305.0, 306.0],
                "最低": [298.0, 299.0],
                "收盘": [303.0, 304.0],
                "成交量": [10000, 12000],
            }
        )

    src = _connected_source(monkeypatch, SimpleNamespace(stock_hk_hist=stock_hk_hist))
    src.subscribe("00700.HK", "1d")
    bars = src.latest_snapshot(2)

    assert called == ["00700"]
    assert len(bars) == 2
    assert bars[0].close == pytest.approx(304.0)

    def boom(**kwargs):
        del kwargs
        raise RuntimeError("network down")

    src = _connected_source(monkeypatch, SimpleNamespace(stock_zh_a_hist_min_em=boom))
    src.subscribe("600519", "1m")
    with pytest.raises(DataSourceTransientError):
        src.latest_snapshot(2)
