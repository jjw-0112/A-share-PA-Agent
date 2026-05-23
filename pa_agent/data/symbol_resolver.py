"""User-facing market symbol resolver for CN/HK stocks, ETFs, and indexes."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Market(str, Enum):
    CN = "CN"
    HK = "HK"


class AssetType(str, Enum):
    STOCK = "stock"
    INDEX = "index"
    ETF = "etf"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResolvedSymbol:
    raw_input: str
    market: Market
    asset_type: AssetType
    code: str
    exchange: str | None
    display_symbol: str
    display_name: str | None = None
    provider_symbols: dict[str, str] = field(default_factory=dict)
    warning: str | None = None

    @property
    def market_symbol(self) -> str:
        if self.market == Market.HK:
            if self.asset_type == AssetType.INDEX:
                return self.code
            return f"{self.code}.HK"
        prefix = self.exchange or _infer_cn_exchange(self.code)
        return f"{prefix}{self.code}" if prefix else self.code


_CN_ETF_PREFIXES = (
    "159",
    "160",
    "161",
    "162",
    "510",
    "511",
    "512",
    "513",
    "515",
    "516",
    "517",
    "518",
    "560",
    "561",
    "562",
    "588",
)
_CN_INDEX_PREFIXES = ("399", "930", "931", "932")
_HK_INDEX_ALIASES: dict[str, tuple[str, str]] = {
    "HSI": ("HSI", "恒生指数"),
    ".HSI": ("HSI", "恒生指数"),
    "HKHSI": ("HSI", "恒生指数"),
    "HSCEI": ("HSCEI", "恒生中国企业指数"),
    ".HSCEI": ("HSCEI", "恒生中国企业指数"),
    "HSTECH": ("HSTECH", "恒生科技指数"),
    ".HSTECH": ("HSTECH", "恒生科技指数"),
}
_HK_ETF_CODES = {"02800", "03033", "02828", "02822", "03067"}


def is_partial_symbol_input(text: str) -> bool:
    """Return True for numeric in-progress codes that should not switch subscription."""
    raw = (text or "").strip()
    if not raw or not raw.isdigit():
        return False
    if len(raw) in (5, 6):
        return False
    return len(raw) < 6


def resolve_symbol(symbol: str) -> ResolvedSymbol:
    """Resolve common quote-app symbols into a canonical market symbol."""
    raw = (symbol or "").strip()
    if not raw:
        raise ValueError("请输入股票、ETF或指数代码")

    token = raw.upper().replace(" ", "")
    prefix = ""
    body = token
    if ":" in token:
        prefix, body = token.split(":", 1)
        prefix = prefix.lower()

    if body in _HK_INDEX_ALIASES:
        code, name = _HK_INDEX_ALIASES[body]
        return _resolved_hk(raw, AssetType.INDEX, code, None, name)

    if body.endswith(".HK"):
        code = body[:-3]
        if code in _HK_INDEX_ALIASES:
            idx_code, name = _HK_INDEX_ALIASES[code]
            return _resolved_hk(raw, AssetType.INDEX, idx_code, None, name)
        if len(code) == 5 and code.isdigit():
            if code == "00000":
                raise ValueError("港股代码不能为 00000")
            return _resolved_hk(raw, _hk_asset_type(code), code, "hk", None)
        raise ValueError(f"不支持的港股代码：{symbol!r}")

    if prefix in ("hk", "h"):
        if body in _HK_INDEX_ALIASES:
            code, name = _HK_INDEX_ALIASES[body]
            return _resolved_hk(raw, AssetType.INDEX, code, None, name)
        if len(body) == 5 and body.isdigit():
            if body == "00000":
                raise ValueError("港股代码不能为 00000")
            return _resolved_hk(raw, _hk_asset_type(body), body, "hk", None)
        raise ValueError(f"不支持的港股代码：{symbol!r}")

    if len(body) == 5 and body.isdigit():
        if body == "00000":
            raise ValueError("港股代码不能为 00000")
        return _resolved_hk(raw, _hk_asset_type(body), body, "hk", None)

    explicit_exchange: str | None = None
    if body.endswith(".SH") or body.endswith(".SZ") or body.endswith(".BJ"):
        explicit_exchange = body[-2:].lower()
        body = body[:-3]
    elif len(body) >= 8 and body[:2].lower() in ("sh", "sz", "bj") and body[2:].isdigit():
        explicit_exchange = body[:2].lower()
        body = body[2:]

    if len(body) != 6 or not body.isdigit():
        raise ValueError(f"不支持的标的代码：{symbol!r}")

    if prefix in ("stock", "stk", "a"):
        asset = AssetType.STOCK
    elif prefix == "etf":
        asset = AssetType.ETF
    elif prefix in ("index", "idx"):
        asset = AssetType.INDEX
    elif prefix:
        raise ValueError(f"不支持的代码前缀：{prefix!r}")
    else:
        asset = _cn_asset_type(body, explicit_exchange)

    exchange = explicit_exchange or _infer_cn_exchange(body)
    warning = None
    if raw == "000001":
        warning = (
            "000001 存在股票/指数歧义，当前按深市股票 sz000001 解析；"
            "如需上证指数请输入 000001.SH 或 sh000001。"
        )
    if explicit_exchange == "sh" and body == "000001":
        asset = AssetType.INDEX
    if explicit_exchange == "sz" and body == "399006":
        asset = AssetType.INDEX

    return _resolved_cn(raw, asset, body, exchange, warning=warning)


def _resolved_cn(
    raw: str,
    asset: AssetType,
    code: str,
    exchange: str | None,
    *,
    warning: str | None = None,
) -> ResolvedSymbol:
    market_symbol = f"{exchange or _infer_cn_exchange(code)}{code}"
    if asset == AssetType.INDEX:
        display = f"INDEX:{market_symbol}"
    elif asset == AssetType.ETF:
        display = f"ETF:{code}"
    else:
        display = f"STOCK:{code}"
    return ResolvedSymbol(
        raw_input=raw,
        market=Market.CN,
        asset_type=asset,
        code=code,
        exchange=exchange,
        display_symbol=display,
        provider_symbols={
            "bare": code,
            "market": market_symbol,
            "akshare": code,
            "sina": market_symbol,
            "tencent": market_symbol,
        },
        warning=warning,
    )


def _resolved_hk(
    raw: str,
    asset: AssetType,
    code: str,
    exchange: str | None,
    name: str | None,
) -> ResolvedSymbol:
    display = code if asset == AssetType.INDEX else f"{code}.HK"
    return ResolvedSymbol(
        raw_input=raw,
        market=Market.HK,
        asset_type=asset,
        code=code,
        exchange=exchange,
        display_symbol=display,
        display_name=name,
        provider_symbols={
            "bare": code,
            "market": f"{code}.HK" if code.isdigit() else code,
            "akshare": code,
            "sina": code,
        },
    )


def _cn_asset_type(code: str, exchange: str | None) -> AssetType:
    if code.startswith(_CN_ETF_PREFIXES):
        return AssetType.ETF
    if code.startswith(_CN_INDEX_PREFIXES):
        return AssetType.INDEX
    if exchange == "sh" and code == "000001":
        return AssetType.INDEX
    return AssetType.STOCK


def _hk_asset_type(code: str) -> AssetType:
    return AssetType.ETF if code in _HK_ETF_CODES or code.startswith(("028", "030")) else AssetType.STOCK


def _infer_cn_exchange(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    if code.startswith(("930", "931", "932")):
        return "sh"
    return ""


__all__ = [
    "AssetType",
    "Market",
    "ResolvedSymbol",
    "is_partial_symbol_input",
    "resolve_symbol",
]
