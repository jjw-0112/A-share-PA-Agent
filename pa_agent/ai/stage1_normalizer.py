"""Normalize common Stage 1 AI JSON variants before schema validation."""
from __future__ import annotations

import copy
import logging
from typing import Any

from pa_agent.ai.trace_normalize import normalize_stage1_traces

logger = logging.getLogger(__name__)

# Common model aliases for on-disk strategy file names.
_STRATEGY_FILE_ALIASES: dict[str, str] = {
    "交易区间分析识别.txt": "震荡区间分析识别.txt",
    "交易区间交易策略.txt": "震荡区间交易策略.txt",
    "宽通道分析识别.txt": "文件13-窄通道与宽通道策略.txt",
    "宽通道交易策略.txt": "文件13-窄通道与宽通道策略.txt",
}

_BAR_ROLE_ALIASES: dict[str, str] = {
    "continue": "confirmation",
    "continued": "confirmation",
    "continuation": "confirmation",
    "follow": "confirmation",
    "follow_through": "confirmation",
    "follow-through": "confirmation",
    "confirm": "confirmation",
    "confirmed": "confirmation",
    "reversal": "signal",
    "breakout": "signal",
    "setup": "signal",
    "pullback": "test",
    "retest": "test",
    "failure": "trap",
    "failed": "trap",
    "exhaustion": "climax",
    "延续": "confirmation",
    "跟随": "confirmation",
    "确认": "confirmation",
    "结构": "structure",
    "信号": "signal",
    "入场": "entry",
    "噪音": "noise",
    "噪声": "noise",
    "陷阱": "trap",
    "高潮": "climax",
    "测试": "test",
}


def _normalize_strategy_file_names(files: Any) -> list[str]:
    if not isinstance(files, list):
        return []
    out: list[str] = []
    for item in files:
        if not isinstance(item, str):
            continue
        name = _STRATEGY_FILE_ALIASES.get(item.strip(), item.strip())
        if name and name not in out:
            out.append(name)
    return out


def _normalize_bar_by_bar_roles(out: dict[str, Any]) -> None:
    summary = out.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return
    for item in summary:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if not isinstance(role, str):
            continue
        key = role.strip().lower()
        normalized = _BAR_ROLE_ALIASES.get(key)
        if normalized:
            item["role"] = normalized
            logger.debug("Mapped bar_by_bar_summary role %r -> %s", role, normalized)


def normalize_stage1(obj: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *obj* with known AI quirks corrected."""
    out = copy.deepcopy(obj)

    if "strategy_files_needed" not in out or out.get("strategy_files_needed") is None:
        alt = out.pop("recommended_strategy_files", None)
        if alt is not None:
            out["strategy_files_needed"] = _normalize_strategy_file_names(alt)
            logger.debug("Mapped recommended_strategy_files -> strategy_files_needed")
        elif out.get("cycle_position") and out.get("direction"):
            try:
                from pa_agent.ai.router import route_strategy_files

                out["strategy_files_needed"] = route_strategy_files(out)
                logger.debug("Filled strategy_files_needed from router")
            except Exception as exc:  # noqa: BLE001
                logger.debug("router fallback for strategy_files_needed failed: %s", exc)
                out.setdefault("strategy_files_needed", [])
    else:
        out["strategy_files_needed"] = _normalize_strategy_file_names(
            out.get("strategy_files_needed")
        )

    normalize_stage1_traces(out)
    _normalize_bar_by_bar_roles(out)

    return out
