"""AKShare-backed public data source for A-share stocks, ETFs, and indexes."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
CACHE_TTL_SECONDS = 5.0

DEFAULT_A_SHARE_SYMBOLS: list[str] = [
    "STOCK:600519",
    "STOCK:300750",
    "STOCK:000001",
    "ETF:510300",
    "ETF:159915",
    "INDEX:sh000001",
    "INDEX:sz399001",
    "INDEX:sz399006",
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

_ETF_PREFIXES = ("15", "16", "18", "51", "56", "58")
_INDEX_MARKET_HINTS: dict[str, str] = {
    "000001": "sh",
    "399001": "sz",
    "399006": "sz",
}


@dataclass(frozen=True, slots=True)
class ParsedAShareSymbol:
    """Normalized user symbol."""

    kind: str  # stock | etf | index
    code: str  # bare 6-digit code, used by most AKShare endpoints
    market: str | None = None  # sh | sz | bj for indexes/market-prefixed inputs

    @property
    def display(self) -> str:
        if self.kind == "index":
            prefix = self.market or _INDEX_MARKET_HINTS.get(self.code, "")
            return f"INDEX:{prefix}{self.code}" if prefix else f"INDEX:{self.code}"
        if self.kind == "etf":
            return f"ETF:{self.code}"
        return f"STOCK:{self.code}"

    @property
    def market_symbol(self) -> str:
        prefix = self.market or _infer_market_prefix(self.code)
        return f"{prefix}{self.code}" if prefix else self.code


class AShareSource(DataSource):
    """Free public A-share K-line data via AKShare.

    The source accepts user-friendly symbols such as ``600519``,
    ``STOCK:600519``, ``ETF:510300``, and ``INDEX:sh000001``. Returned bars are
    newest-first. During continuous trading the newest minute/day bar may be
    marked ``closed=False``; during lunch, after close, weekends, or other
    non-continuous sessions the latest returned bar is treated as closed.
    """

    def __init__(self, *, cache_ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._symbol: str = ""
        self._timeframe: str = ""
        self._parsed: ParsedAShareSymbol | None = None
        self._connected: bool = False
        self._ak: Any = None
        self._cache_ttl_seconds = float(cache_ttl_seconds)
        self._cache: dict[tuple[str, str, int], tuple[float, list[KlineBar]]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            import akshare as ak  # type: ignore[import]
        except ImportError as exc:
            raise DataSourceTransientError(
                "akshare not installed - run: pip install akshare"
            ) from exc
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
            parse_a_share_symbol(symbol)
        except ValueError:
            return False
        return True

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, symbol: str, timeframe: str) -> None:
        if timeframe not in _MINUTE_TF_MAP and timeframe not in _DAILY_TF_MAP:
            raise ValueError(
                f"Unsupported A-share timeframe: {timeframe!r}. "
                f"Use one of {self.supported_timeframes()}"
            )
        parsed = parse_a_share_symbol(symbol)
        self._symbol = parsed.display
        self._timeframe = timeframe
        self._parsed = parsed
        self._cache.clear()
        logger.info("AShareSource subscribed: %s %s", self._symbol, timeframe)

    def unsubscribe(self) -> None:
        self._symbol = ""
        self._timeframe = ""
        self._parsed = None
        self._cache.clear()
        logger.info("AShareSource unsubscribed")

    def server_time_ms(self, symbol: str | None = None) -> int | None:
        del symbol
        return int(datetime.now(CN_TZ).timestamp() * 1000)

    def session_status(self) -> str:
        return "连续竞价中" if _is_continuous_trading_time() else "非连续竞价时段/等待下一根K线"

    # ── Data fetch ────────────────────────────────────────────────────────────

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        if not self._connected or self._ak is None:
            raise DataSourceTransientError("Not connected - call connect() first")
        if not self._parsed or not self._timeframe:
            raise DataSourceTransientError("Not subscribed - call subscribe() first")

        cache_key = (self._symbol, self._timeframe, int(n))
        now_mono = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now_mono - cached[0] < self._cache_ttl_seconds:
            return list(cached[1])

        try:
            raw_df = self._fetch_dataframe(self._ak, self._parsed, self._timeframe, n)
            norm_df = _normalize_ohlcv(raw_df)
            if self._parsed.kind == "index" and self._timeframe in ("1w", "1M"):
                norm_df = _resample_normalized_index(norm_df, self._timeframe)
            bars = _build_bars(norm_df, self._timeframe, n)
        except DataSourceTransientError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DataSourceTransientError(
                f"AKShare fetch failed for {self._symbol} {self._timeframe}: {exc}"
            ) from exc

        if not bars:
            raise DataSourceTransientError(
                f"AKShare returned no usable data for {self._symbol} {self._timeframe}"
            )
        self._cache[cache_key] = (now_mono, list(bars))
        return bars

    def _fetch_dataframe(
        self,
        ak: Any,
        parsed: ParsedAShareSymbol,
        timeframe: str,
        n: int,
    ) -> Any:
        if timeframe in _MINUTE_TF_MAP:
            return self._fetch_minute_dataframe(ak, parsed, timeframe, n)
        return self._fetch_period_dataframe(ak, parsed, timeframe)

    def _fetch_minute_dataframe(
        self,
        ak: Any,
        parsed: ParsedAShareSymbol,
        timeframe: str,
        n: int,
    ) -> Any:
        period = _MINUTE_TF_MAP[timeframe]
        now = datetime.now(CN_TZ)
        lookback_days = max(10, min(180, int(n / 48) + 10))
        start_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d 09:30:00")
        end_date = (now + timedelta(days=1)).strftime("%Y-%m-%d 15:00:00")

        if parsed.kind == "stock":
            try:
                return ak.stock_zh_a_hist_min_em(
                    symbol=parsed.code,
                    period=period,
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                )
            except Exception as exc:  # noqa: BLE001
                fallback = getattr(ak, "stock_zh_a_minute", None)
                if not callable(fallback):
                    raise
                logger.debug(
                    "AKShare stock_zh_a_hist_min_em failed for %s, trying stock_zh_a_minute: %s",
                    parsed.code,
                    exc,
                )
                return fallback(symbol=parsed.market_symbol, period=period, adjust="")
        if parsed.kind == "etf":
            return ak.fund_etf_hist_min_em(
                symbol=parsed.code,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
        return ak.index_zh_a_hist_min_em(
            symbol=parsed.code,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )

    def _fetch_period_dataframe(
        self,
        ak: Any,
        parsed: ParsedAShareSymbol,
        timeframe: str,
    ) -> Any:
        period = _DAILY_TF_MAP[timeframe]
        now = datetime.now(CN_TZ)
        start_date = (now - timedelta(days=365 * 8)).strftime("%Y%m%d")
        end_date = now.strftime("%Y%m%d")

        if parsed.kind == "stock":
            return ak.stock_zh_a_hist(
                symbol=parsed.code,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
        if parsed.kind == "etf":
            return ak.fund_etf_hist_em(
                symbol=parsed.code,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
        return _fetch_index_daily(ak, parsed, start_date, end_date)


def parse_a_share_symbol(symbol: str) -> ParsedAShareSymbol:
    """Parse supported A-share symbol forms."""
    raw = (symbol or "").strip()
    if not raw:
        raise ValueError("A-share symbol is empty")

    prefix = ""
    body = raw
    if ":" in raw:
        prefix, body = raw.split(":", 1)
        prefix = prefix.strip().lower()
        body = body.strip()

    market = None
    body_l = body.lower()
    if len(body_l) >= 8 and body_l[:2] in ("sh", "sz", "bj") and body_l[2:].isdigit():
        market = body_l[:2]
        code = body_l[2:]
    else:
        code = body_l

    if not (len(code) == 6 and code.isdigit()):
        raise ValueError(f"Unsupported A-share symbol: {symbol!r}")

    if prefix in ("stock", "stk", "a"):
        return ParsedAShareSymbol("stock", code, market)
    if prefix == "etf":
        return ParsedAShareSymbol("etf", code, market)
    if prefix in ("index", "idx"):
        return ParsedAShareSymbol("index", code, market or _INDEX_MARKET_HINTS.get(code))
    if prefix:
        raise ValueError(f"Unsupported A-share symbol prefix: {prefix!r}")

    if market and code.startswith(("000", "399")):
        return ParsedAShareSymbol("index", code, market)
    if code.startswith(_ETF_PREFIXES):
        return ParsedAShareSymbol("etf", code, market)
    return ParsedAShareSymbol("stock", code, market)


def _fetch_index_daily(ak: Any, parsed: ParsedAShareSymbol, start_date: str, end_date: str) -> Any:
    symbol_market = parsed.market_symbol
    attempts = (
        ("stock_zh_index_daily_em", {"symbol": symbol_market}),
        ("stock_zh_index_daily", {"symbol": symbol_market}),
        (
            "stock_zh_index_daily_tx",
            {"symbol": symbol_market, "start_date": start_date, "end_date": end_date},
        ),
    )
    last_exc: Exception | None = None
    for name, kwargs in attempts:
        func = getattr(ak, name, None)
        if not callable(func):
            continue
        try:
            return func(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.debug("AKShare %s(%s) failed: %s", name, kwargs, exc)
    raise DataSourceTransientError(
        f"AKShare index daily endpoint unavailable for {symbol_market}: {last_exc}"
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

    required = {
        "time": time_col,
        "open": open_col,
        "close": close_col,
        "high": high_col,
        "low": low_col,
    }
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
            "volume": (
                pd.to_numeric(frame[volume_col], errors="coerce")
                if volume_col is not None
                else 0.0
            ),
        }
    )
    out = out.dropna(subset=["ts", "open", "high", "low", "close"])
    if out.empty:
        raise DataSourceTransientError("AKShare DataFrame has no usable OHLC rows")
    out["volume"] = out["volume"].fillna(0.0)
    return out.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")


def _resample_normalized_index(df: Any, timeframe: str) -> Any:
    freq = "W-FRI" if timeframe == "1w" else "ME"
    resampled = (
        df.set_index("ts")
        .resample(freq)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
        .reset_index()
    )
    return resampled


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


__all__ = ["AShareSource", "DEFAULT_A_SHARE_SYMBOLS", "ParsedAShareSymbol", "parse_a_share_symbol"]
