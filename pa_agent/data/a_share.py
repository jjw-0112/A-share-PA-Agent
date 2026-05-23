"""AKShare-backed public market source for A/HK stocks, ETFs, and indexes."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar
from pa_agent.data.hk_share import hk_fetch_attempts
from pa_agent.data.market_status import DataStatusLevel, MarketDataStatus
from pa_agent.data.proxy import no_proxy_env
from pa_agent.data.symbol_resolver import AssetType, Market, ResolvedSymbol, resolve_symbol

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
CACHE_TTL_SECONDS = 5.0

DEFAULT_A_SHARE_SYMBOLS: list[str] = [
    "600519",
    "300750",
    "000001",
    "510300",
    "159915",
    "000001.SH",
    "399001",
    "399006",
    "00700.HK",
    "09988.HK",
    "02800.HK",
    "HSI",
    "HSTECH",
]

_MINUTE_TF_MAP: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
}

_DAILY_TF_MAP: dict[str, str] = {
    "1d": "daily",
    "1w": "weekly",
    "1M": "monthly",
}


@dataclass(frozen=True, slots=True)
class ParsedAShareSymbol:
    """Backward-compatible normalized A-share symbol."""

    kind: str
    code: str
    market: str | None = None

    @property
    def display(self) -> str:
        if self.kind == "index":
            return f"INDEX:{self.market or ''}{self.code}"
        if self.kind == "etf":
            return f"ETF:{self.code}"
        return f"STOCK:{self.code}"

    @property
    def market_symbol(self) -> str:
        prefix = self.market or _infer_market_prefix(self.code)
        return f"{prefix}{self.code}" if prefix else self.code


@dataclass(frozen=True, slots=True)
class DataProviderResult:
    provider: str
    df: Any
    warning: str | None = None
    latency_ms: int | None = None


class DataFetchError(Exception):
    def __init__(self, provider: str, message: str, cause: Exception | None = None) -> None:
        self.provider = provider
        self.cause = cause
        super().__init__(message)


class _StaleMarketDataError(DataSourceTransientError):
    """Returned data belongs to an obsolete subscription generation."""


class AShareSource(DataSource):
    """Free public A/HK K-line data via AKShare with provider fallback."""

    def __init__(
        self,
        *,
        cache_ttl_seconds: float = CACHE_TTL_SECONDS,
        use_proxy: bool = False,
        timeout_sec: float = 8.0,
        retry: int = 1,
    ) -> None:
        self._symbol: str = ""
        self._timeframe: str = ""
        self._resolved: ResolvedSymbol | None = None
        self._connected: bool = False
        self._ak: Any = None
        self._cache_ttl_seconds = float(cache_ttl_seconds)
        self._cache: dict[tuple[str, str, int, int], tuple[float, list[KlineBar]]] = {}
        self._use_proxy = bool(use_proxy)
        self._timeout_sec = float(timeout_sec)
        self._retry = max(1, int(retry))
        self._generation_id = 0
        self._request_id = 0
        self._error_count = 0
        self.last_status: MarketDataStatus = MarketDataStatus(
            level=DataStatusLevel.IDLE,
            message="等待行情请求",
        )
        self.last_provider: str | None = None
        self.last_warning: str | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            import akshare as ak  # type: ignore[import]
        except ImportError as exc:
            raise DataSourceTransientError("akshare not installed - run: pip install akshare") from exc
        self._ak = ak
        self._connected = True
        logger.info("AShareSource connected (AKShare available)")

    def disconnect(self) -> None:
        self._connected = False
        self._ak = None
        self._cache.clear()
        logger.info("AShareSource disconnected")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_symbols(self) -> list[str]:
        return list(DEFAULT_A_SHARE_SYMBOLS)

    def supported_timeframes(self) -> list[str]:
        return [*list(_MINUTE_TF_MAP), *list(_DAILY_TF_MAP)]

    def is_symbol_available(self, symbol: str) -> bool:
        try:
            resolve_symbol(symbol)
        except ValueError:
            return False
        return True

    def prepare_subscription(self, symbol: str, timeframe: str) -> ResolvedSymbol:
        if timeframe not in _MINUTE_TF_MAP and timeframe not in _DAILY_TF_MAP:
            raise ValueError(f"不支持的周期：{timeframe}")
        return resolve_symbol(symbol)

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, symbol: str, timeframe: str) -> None:
        resolved = self.prepare_subscription(symbol, timeframe)
        self._generation_id += 1
        self._symbol = resolved.display_symbol
        self._timeframe = timeframe
        self._resolved = resolved
        self._cache.clear()
        self._set_status(
            DataStatusLevel.IDLE,
            raw_input=symbol,
            resolved=resolved,
            timeframe=timeframe,
            message=f"已解析为 {resolved.display_symbol}",
            warning=resolved.warning,
        )
        logger.info(
            "[market-data generation=%s symbol=%s tf=%s] subscribed raw=%s",
            self._generation_id,
            resolved.display_symbol,
            timeframe,
            symbol,
        )

    def unsubscribe(self) -> None:
        self._generation_id += 1
        self._symbol = ""
        self._timeframe = ""
        self._resolved = None
        self._cache.clear()
        self._set_status(DataStatusLevel.IDLE, message="未订阅")
        logger.info("AShareSource unsubscribed")

    def server_time_ms(self, symbol: str | None = None) -> int | None:
        del symbol
        return int(datetime.now(CN_TZ).timestamp() * 1000)

    def session_status(self) -> str:
        resolved = self._resolved
        if resolved is not None and resolved.market == Market.HK:
            return "港股非连续竞价时段/等待下一根K线"
        return "连续竞价中" if _is_continuous_trading_time() else "非连续竞价时段/等待下一根K线"

    # ── Data fetch ────────────────────────────────────────────────────────────

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        if not self._connected or self._ak is None:
            raise DataSourceTransientError("Not connected - call connect() first")
        if not self._resolved or not self._timeframe:
            raise DataSourceTransientError("Not subscribed - call subscribe() first")

        request_id = self._next_request_id()
        generation_id = self._generation_id
        resolved = self._resolved
        timeframe = self._timeframe
        cache_key = (resolved.display_symbol, timeframe, int(n), generation_id)
        now_mono = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now_mono - cached[0] < self._cache_ttl_seconds:
            return list(cached[1])

        self._set_status(
            DataStatusLevel.FETCHING,
            raw_input=resolved.raw_input,
            resolved=resolved,
            timeframe=timeframe,
            bar_count=int(n),
            message=f"正在获取 {resolved.display_symbol} {timeframe} 行情...",
            request_id=request_id,
            generation_id=generation_id,
        )

        logger.info(
            "[market-data request_id=%s generation=%s symbol=%s tf=%s bars=%s] fetching",
            request_id,
            generation_id,
            resolved.display_symbol,
            timeframe,
            n,
        )
        t0 = time.monotonic()
        try:
            result = self._fetch_dataframe_with_fallback(self._ak, resolved, timeframe, int(n), request_id, generation_id)
            if generation_id != self._generation_id:
                raise _StaleMarketDataError("stale market data result discarded")
            norm_df = _normalize_ohlcv(result.df)
            if timeframe in ("1w", "1M"):
                norm_df = _resample_normalized_index(norm_df, timeframe)
            bars = _build_bars(norm_df, timeframe, n)
        except _StaleMarketDataError:
            logger.info(
                "[market-data request_id=%s generation=%s symbol=%s tf=%s] stale result discarded",
                request_id,
                generation_id,
                resolved.display_symbol,
                timeframe,
            )
            raise
        except DataSourceTransientError as exc:
            self._error_count += 1
            self._set_status(
                DataStatusLevel.ERROR,
                raw_input=resolved.raw_input,
                resolved=resolved,
                timeframe=timeframe,
                bar_count=int(n),
                message="行情获取失败",
                provider=self.last_provider,
                latency_ms=int((time.monotonic() - t0) * 1000),
                last_error=str(exc),
                request_id=request_id,
                generation_id=generation_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            wrapped = DataSourceTransientError(
                f"AKShare fetch failed for {resolved.display_symbol} {timeframe}: {exc}"
            )
            self._set_status(
                DataStatusLevel.ERROR,
                raw_input=resolved.raw_input,
                resolved=resolved,
                timeframe=timeframe,
                bar_count=int(n),
                message="行情获取失败",
                provider=self.last_provider,
                latency_ms=int((time.monotonic() - t0) * 1000),
                last_error=str(wrapped),
                request_id=request_id,
                generation_id=generation_id,
            )
            raise wrapped from exc

        if not bars:
            raise DataSourceTransientError(f"AKShare returned no usable data for {resolved.display_symbol} {timeframe}")

        self.last_provider = result.provider
        self.last_warning = result.warning
        self._cache[cache_key] = (now_mono, list(bars))
        level = DataStatusLevel.WARNING if result.warning else DataStatusLevel.SUCCESS
        latest_dt = _bar_dt(bars[0])
        self._set_status(
            level,
            raw_input=resolved.raw_input,
            resolved=resolved,
            timeframe=timeframe,
            bar_count=int(n),
            provider=result.provider,
            message=f"获取成功，返回 {len(bars)} 根K线",
            bars_returned=len(bars),
            latest_bar_time=latest_dt,
            latency_ms=int((time.monotonic() - t0) * 1000),
            warning=result.warning,
            request_id=request_id,
            generation_id=generation_id,
            last_success_at=datetime.now(),
        )
        logger.info(
            "[market-data request_id=%s generation=%s provider=%s symbol=%s tf=%s] success bars=%s latest=%s",
            request_id,
            generation_id,
            result.provider,
            resolved.display_symbol,
            timeframe,
            len(bars),
            latest_dt,
        )
        return bars

    def _fetch_dataframe_with_fallback(
        self,
        ak: Any,
        resolved: ResolvedSymbol,
        timeframe: str,
        n: int,
        request_id: int,
        generation_id: int,
    ) -> DataProviderResult:
        attempts = self._provider_attempts(ak, resolved, timeframe, n)
        failures: list[str] = []
        for provider, func, kwargs in attempts:
            if generation_id != self._generation_id:
                raise _StaleMarketDataError("stale market data request")
            if not callable(func):
                failures.append(f"{provider}: endpoint unavailable")
                continue
            for retry_idx in range(self._retry):
                start = time.monotonic()
                try:
                    with no_proxy_env(enabled=not self._use_proxy):
                        df = func(**kwargs)
                    _normalize_ohlcv(df)
                    latency = int((time.monotonic() - start) * 1000)
                    warning = None
                    if failures:
                        warning = f"{failures[0]}，已使用 {provider} 备用源"
                    if retry_idx > 0:
                        warning = warning or f"{provider} 第 {retry_idx + 1} 次重试成功"
                    self.last_provider = provider
                    self.last_warning = warning
                    return DataProviderResult(provider=provider, df=df, warning=warning, latency_ms=latency)
                except Exception as exc:  # noqa: BLE001
                    msg = f"{provider}: {type(exc).__name__}: {exc}"
                    failures.append(msg)
                    logger.warning(
                        "[market-data request_id=%s generation=%s provider=%s symbol=%s tf=%s] failed: %s",
                        request_id,
                        generation_id,
                        provider,
                        resolved.display_symbol,
                        timeframe,
                        exc,
                    )
        raise DataSourceTransientError("; ".join(failures) or "No provider available")

    def _provider_attempts(
        self,
        ak: Any,
        resolved: ResolvedSymbol,
        timeframe: str,
        n: int,
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        now = datetime.now(CN_TZ)
        start_date = (now - timedelta(days=365 * 8)).strftime("%Y%m%d")
        end_date = now.strftime("%Y%m%d")
        if resolved.market == Market.HK:
            period = _MINUTE_TF_MAP.get(timeframe) or _DAILY_TF_MAP[timeframe]
            return hk_fetch_attempts(ak, resolved, timeframe, period, start_date, end_date)

        if timeframe in _MINUTE_TF_MAP:
            return _cn_minute_attempts(ak, resolved, timeframe, n)
        return _cn_period_attempts(ak, resolved, timeframe, start_date, end_date)

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _set_status(
        self,
        level: DataStatusLevel,
        *,
        raw_input: str = "",
        resolved: ResolvedSymbol | None = None,
        timeframe: str = "",
        bar_count: int = 0,
        provider: str | None = None,
        message: str = "",
        bars_returned: int | None = None,
        latest_bar_time: datetime | None = None,
        latency_ms: int | None = None,
        warning: str | None = None,
        last_error: str | None = None,
        request_id: int | None = None,
        generation_id: int | None = None,
        last_success_at: datetime | None = None,
    ) -> None:
        self.last_status = MarketDataStatus(
            level=level,
            raw_input=raw_input or (resolved.raw_input if resolved else ""),
            resolved_symbol=resolved.display_symbol if resolved else None,
            market=resolved.market.value if resolved else None,
            asset_type=resolved.asset_type.value if resolved else None,
            timeframe=timeframe or self._timeframe,
            bar_count=bar_count,
            provider=provider,
            message=message,
            last_request_at=datetime.now() if level == DataStatusLevel.FETCHING else self.last_status.last_request_at,
            last_success_at=last_success_at or self.last_status.last_success_at,
            bars_returned=bars_returned,
            latest_bar_time=latest_bar_time,
            latency_ms=latency_ms,
            error_count=self._error_count,
            last_error=last_error,
            warning=warning,
            request_id=request_id,
            generation_id=generation_id,
        )


def _cn_minute_attempts(
    ak: Any,
    resolved: ResolvedSymbol,
    timeframe: str,
    n: int,
) -> list[tuple[str, Any, dict[str, Any]]]:
    period = _MINUTE_TF_MAP[timeframe]
    now = datetime.now(CN_TZ)
    lookback_days = max(10, min(180, int(n / 48) + 10))
    start = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d 09:30:00")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d 15:00:00")
    if resolved.asset_type == AssetType.STOCK:
        return [
            ("eastmoney-stock-minute", getattr(ak, "stock_zh_a_hist_min_em", None), {"symbol": resolved.code, "period": period, "start_date": start, "end_date": end, "adjust": ""}),
            ("sina-stock-minute", getattr(ak, "stock_zh_a_minute", None), {"symbol": resolved.market_symbol, "period": period, "adjust": ""}),
        ]
    if resolved.asset_type == AssetType.ETF:
        return [
            ("eastmoney-etf-minute", getattr(ak, "fund_etf_hist_min_em", None), {"symbol": resolved.code, "period": period, "start_date": start, "end_date": end, "adjust": ""}),
            ("sina-etf-daily-fallback", getattr(ak, "fund_etf_hist_sina", None), {"symbol": resolved.market_symbol}),
        ]
    return [
        ("eastmoney-index-minute", getattr(ak, "index_zh_a_hist_min_em", None), {"symbol": resolved.code, "period": period, "start_date": start, "end_date": end}),
        ("akshare-index-daily-fallback", getattr(ak, "index_zh_a_hist", None), {"symbol": resolved.code, "period": "daily", "start_date": (now - timedelta(days=365 * 3)).strftime("%Y%m%d"), "end_date": now.strftime("%Y%m%d")}),
    ]


def _cn_period_attempts(
    ak: Any,
    resolved: ResolvedSymbol,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, Any, dict[str, Any]]]:
    period = _DAILY_TF_MAP[timeframe]
    if resolved.asset_type == AssetType.STOCK:
        return [
            ("eastmoney-stock-daily", getattr(ak, "stock_zh_a_hist", None), {"symbol": resolved.code, "period": period, "start_date": start_date, "end_date": end_date, "adjust": "", "timeout": 8}),
            ("sina-stock-daily", getattr(ak, "stock_zh_a_daily", None), {"symbol": resolved.market_symbol, "start_date": start_date, "end_date": end_date, "adjust": ""}),
            ("tencent-stock-daily", getattr(ak, "stock_zh_a_hist_tx", None), {"symbol": resolved.market_symbol, "start_date": start_date, "end_date": end_date, "adjust": "", "timeout": 8}),
        ]
    if resolved.asset_type == AssetType.ETF:
        return [
            ("eastmoney-etf-daily", getattr(ak, "fund_etf_hist_em", None), {"symbol": resolved.code, "period": period, "start_date": start_date, "end_date": end_date, "adjust": ""}),
            ("sina-etf-daily", getattr(ak, "fund_etf_hist_sina", None), {"symbol": resolved.market_symbol}),
        ]
    return [
        ("akshare-index-daily", getattr(ak, "index_zh_a_hist", None), {"symbol": resolved.code, "period": period, "start_date": start_date, "end_date": end_date}),
        ("eastmoney-index-daily", getattr(ak, "stock_zh_index_daily_em", None), {"symbol": resolved.market_symbol}),
        ("sina-index-daily", getattr(ak, "stock_zh_index_daily", None), {"symbol": resolved.market_symbol}),
        ("tencent-index-daily", getattr(ak, "stock_zh_index_daily_tx", None), {"symbol": resolved.market_symbol, "start_date": start_date, "end_date": end_date}),
    ]


def parse_a_share_symbol(symbol: str) -> ParsedAShareSymbol:
    """Parse supported A-share symbol forms, kept for older callers/tests."""
    resolved = resolve_symbol(symbol)
    if resolved.market != Market.CN:
        raise ValueError(f"Unsupported A-share symbol: {symbol!r}")
    return ParsedAShareSymbol(
        kind=resolved.asset_type.value,
        code=resolved.code,
        market=resolved.exchange,
    )


def _normalize_ohlcv(df: Any) -> Any:
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        raise DataSourceTransientError("AKShare returned an empty DataFrame")

    frame = df.copy()
    if isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reset_index()

    time_col = _pick_col(frame, "时间", "日期", "date", "day", "datetime", "timestamp", "index")
    open_col = _pick_col(frame, "开盘", "open", "Open")
    close_col = _pick_col(frame, "收盘", "close", "Close")
    high_col = _pick_col(frame, "最高", "high", "High")
    low_col = _pick_col(frame, "最低", "low", "Low")
    volume_col = _pick_col(frame, "成交量", "volume", "Volume", "vol")

    required = {"time": time_col, "open": open_col, "close": close_col, "high": high_col, "low": low_col}
    missing = [name for name, col in required.items() if col is None]
    if missing:
        raise DataSourceTransientError(
            f"AKShare DataFrame missing OHLC columns: {missing}; columns={list(frame.columns)}"
        )

    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(frame[time_col], errors="coerce"),
            "open": pd.to_numeric(frame[open_col], errors="coerce"),
            "high": pd.to_numeric(frame[high_col], errors="coerce"),
            "low": pd.to_numeric(frame[low_col], errors="coerce"),
            "close": pd.to_numeric(frame[close_col], errors="coerce"),
            "volume": pd.to_numeric(frame[volume_col], errors="coerce") if volume_col is not None else 0.0,
        }
    )
    out = out.dropna(subset=["ts", "open", "high", "low", "close"])
    if out.empty:
        raise DataSourceTransientError("AKShare DataFrame has no usable OHLC rows")
    out["volume"] = out["volume"].fillna(0.0)
    return out.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")


def _resample_normalized_index(df: Any, timeframe: str) -> Any:
    freq = "W-FRI" if timeframe == "1w" else "ME"
    return (
        df.set_index("ts")
        .resample(freq)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def _build_bars(df: Any, timeframe: str, n: int) -> list[KlineBar]:
    tail = df.tail(max(int(n), 1)).iloc[::-1].reset_index(drop=True)
    bars: list[KlineBar] = []
    now = datetime.now(CN_TZ)
    for i, row in tail.iterrows():
        ts_ms = _timestamp_ms(row["ts"])
        bars.append(
            KlineBar(
                seq=int(i) + 1,
                ts_open=ts_ms,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0) or 0.0),
                closed=_is_bar_closed(ts_ms, timeframe, now=now),
            )
        )
    return bars


def _pick_col(df: Any, *names: str) -> Any | None:
    exact = {str(col): col for col in df.columns}
    lowered = {str(col).lower(): col for col in df.columns}
    for name in names:
        if name in exact:
            return exact[name]
        key = name.lower()
        if key in lowered:
            return lowered[key]
    return None


def _timestamp_ms(value: Any) -> int:
    dt = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(dt, datetime):
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    else:
        dt = dt.astimezone(CN_TZ)
    return int(dt.timestamp() * 1000)


def _is_bar_closed(ts_ms: int, timeframe: str, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(CN_TZ)
    bar_dt = datetime.fromtimestamp(ts_ms / 1000, tz=CN_TZ)
    if timeframe in _MINUTE_TF_MAP:
        if not _is_continuous_trading_time(now):
            return True
        minutes = 60 if timeframe == "1h" else int(timeframe[:-1])
        return now >= bar_dt + timedelta(minutes=minutes)
    if timeframe == "1d":
        if bar_dt.date() < now.date():
            return True
        return not _is_continuous_trading_time(now) and now.time() >= dtime(15, 0)
    return True


def _is_continuous_trading_time(now: datetime | None = None) -> bool:
    now = now or datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dtime(9, 30) <= t < dtime(11, 30)) or (dtime(13, 0) <= t < dtime(15, 0))


def _infer_market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return ""


def _bar_dt(bar: KlineBar) -> datetime:
    ts = float(bar.ts_open)
    if abs(ts) > 10_000_000_000:
        ts /= 1000.0
    return datetime.fromtimestamp(ts)


__all__ = [
    "AShareSource",
    "DEFAULT_A_SHARE_SYMBOLS",
    "DataFetchError",
    "DataProviderResult",
    "ParsedAShareSymbol",
    "parse_a_share_symbol",
]
