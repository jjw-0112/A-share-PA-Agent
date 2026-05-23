"""Tests for quote-app style CN/HK symbol resolution."""
from __future__ import annotations

import pytest

from pa_agent.data.symbol_resolver import AssetType, Market, resolve_symbol


@pytest.mark.parametrize(
    ("raw", "market", "asset_type", "market_symbol", "display"),
    [
        ("000006", Market.CN, AssetType.STOCK, "sz000006", "STOCK:000006"),
        ("600519", Market.CN, AssetType.STOCK, "sh600519", "STOCK:600519"),
        ("300750", Market.CN, AssetType.STOCK, "sz300750", "STOCK:300750"),
        ("688981", Market.CN, AssetType.STOCK, "sh688981", "STOCK:688981"),
        ("510300", Market.CN, AssetType.ETF, "sh510300", "ETF:510300"),
        ("159915", Market.CN, AssetType.ETF, "sz159915", "ETF:159915"),
        ("399006", Market.CN, AssetType.INDEX, "sz399006", "INDEX:sz399006"),
        ("000001.SH", Market.CN, AssetType.INDEX, "sh000001", "INDEX:sh000001"),
        ("000001.SZ", Market.CN, AssetType.STOCK, "sz000001", "STOCK:000001"),
        ("931140", Market.CN, AssetType.INDEX, "sh931140", "INDEX:sh931140"),
        ("STOCK:600519", Market.CN, AssetType.STOCK, "sh600519", "STOCK:600519"),
        ("ETF:510300", Market.CN, AssetType.ETF, "sh510300", "ETF:510300"),
        ("INDEX:sh000001", Market.CN, AssetType.INDEX, "sh000001", "INDEX:sh000001"),
        ("00700", Market.HK, AssetType.STOCK, "00700.HK", "00700.HK"),
        ("00700.HK", Market.HK, AssetType.STOCK, "00700.HK", "00700.HK"),
        ("02800.HK", Market.HK, AssetType.ETF, "02800.HK", "02800.HK"),
        ("HSI", Market.HK, AssetType.INDEX, "HSI", "HSI"),
        ("HSTECH", Market.HK, AssetType.INDEX, "HSTECH", "HSTECH"),
    ],
)
def test_resolve_common_cn_hk_symbols(raw, market, asset_type, market_symbol, display) -> None:
    resolved = resolve_symbol(raw)
    assert resolved.market == market
    assert resolved.asset_type == asset_type
    assert resolved.market_symbol == market_symbol
    assert resolved.display_symbol == display


def test_ambiguous_000001_defaults_to_sz_stock_with_warning() -> None:
    resolved = resolve_symbol("000001")
    assert resolved.market_symbol == "sz000001"
    assert resolved.asset_type == AssetType.STOCK
    assert resolved.warning and "歧义" in resolved.warning


def test_invalid_mid_edit_00000_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_symbol("00000")
