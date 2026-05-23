"""HK market AKShare helper functions used by the public market source."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pa_agent.data.symbol_resolver import AssetType, ResolvedSymbol


_HK_INDEX_SINA_SYMBOLS: dict[str, str] = {
    "HSI": "HSI",
    "HSCEI": "HSCEI",
    "HSTECH": "HSTECH",
}


def hk_fetch_attempts(
    ak: Any,
    resolved: ResolvedSymbol,
    timeframe: str,
    period: str,
    start_date: str,
    end_date: str,
    *,
    minute_start: str | None = None,
    minute_end: str | None = None,
) -> list[tuple[str, Any, dict[str, Any]]]:
    """Return ordered HK provider attempts as (provider, function, kwargs)."""
    if resolved.asset_type == AssetType.INDEX:
        code = _HK_INDEX_SINA_SYMBOLS.get(resolved.code.upper(), resolved.code.upper())
        if timeframe in {"1m", "5m", "15m", "30m", "1h"}:
            # AKShare has no broadly reliable minute endpoint for all HK indexes.
            return [
                ("akshare-hk-index-daily-em", getattr(ak, "stock_hk_index_daily_em", None), {"symbol": code}),
                ("sina-hk-index-daily", getattr(ak, "stock_hk_index_daily_sina", None), {"symbol": code}),
            ]
        return [
            ("akshare-hk-index-daily-em", getattr(ak, "stock_hk_index_daily_em", None), {"symbol": code}),
            ("sina-hk-index-daily", getattr(ak, "stock_hk_index_daily_sina", None), {"symbol": code}),
        ]

    if timeframe in {"1m", "5m", "15m", "30m", "1h"}:
        return [
            (
                "eastmoney-hk-minute",
                getattr(ak, "stock_hk_hist_min_em", None),
                {
                    "symbol": resolved.code,
                    "period": period,
                    "start_date": minute_start or _minute_start_date(),
                    "end_date": minute_end or _minute_end_date(),
                    "adjust": "",
                },
            ),
            ("sina-hk-daily-fallback", getattr(ak, "stock_hk_daily", None), {"symbol": resolved.code, "adjust": ""}),
        ]

    return [
        (
            "eastmoney-hk-daily",
            getattr(ak, "stock_hk_hist", None),
            {
                "symbol": resolved.code,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "adjust": "",
            },
        ),
        ("sina-hk-daily", getattr(ak, "stock_hk_daily", None), {"symbol": resolved.code, "adjust": ""}),
    ]


def _minute_start_date() -> str:
    return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d 09:30:00")


def _minute_end_date() -> str:
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 16:30:00")


__all__ = ["hk_fetch_attempts"]
