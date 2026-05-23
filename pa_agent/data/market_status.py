"""Market data and analysis status objects for UI feedback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DataStatusLevel(str, Enum):
    IDLE = "idle"
    FETCHING = "fetching"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MarketDataStatus:
    level: DataStatusLevel
    raw_input: str = ""
    resolved_symbol: str | None = None
    market: str | None = None
    asset_type: str | None = None
    timeframe: str = ""
    bar_count: int = 0
    provider: str | None = None
    message: str = ""
    last_request_at: datetime | None = None
    last_success_at: datetime | None = None
    bars_returned: int | None = None
    latest_bar_time: datetime | None = None
    latency_ms: int | None = None
    error_count: int = 0
    last_error: str | None = None
    warning: str | None = None
    request_id: int | None = None
    generation_id: int | None = None


class AnalysisStage(str, Enum):
    IDLE = "idle"
    PREPARING_SNAPSHOT = "preparing_snapshot"
    USING_CACHE = "using_cache"
    FETCHING_SNAPSHOT = "fetching_snapshot"
    BUILDING_PROMPT = "building_prompt"
    CALLING_LLM = "calling_llm"
    VALIDATING_JSON = "validating_json"
    RENDERING = "rendering"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AnalysisStatus:
    stage: AnalysisStage
    message: str
    symbol: str | None = None
    timeframe: str | None = None
    bars_count: int | None = None
    provider: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None


__all__ = [
    "AnalysisStage",
    "AnalysisStatus",
    "DataStatusLevel",
    "MarketDataStatus",
]
