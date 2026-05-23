"""Proxy helpers for public market data calls."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")
_MARKET_NO_PROXY = (
    "eastmoney.com",
    "push2his.eastmoney.com",
    "sina.com.cn",
    "quotes.sina.cn",
    "qq.com",
    "gtimg.cn",
)


@contextmanager
def no_proxy_env(*, enabled: bool = True) -> Iterator[None]:
    """Temporarily remove proxy env vars and protect common quote domains."""
    if not enabled:
        yield
        return

    old = {key: os.environ.get(key) for key in (*_PROXY_KEYS, *_NO_PROXY_KEYS)}
    for key in _PROXY_KEYS:
        os.environ.pop(key, None)
    no_proxy_value = ",".join(_MARKET_NO_PROXY)
    os.environ["NO_PROXY"] = no_proxy_value
    os.environ["no_proxy"] = no_proxy_value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


__all__ = ["no_proxy_env"]
