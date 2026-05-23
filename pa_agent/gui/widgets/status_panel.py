"""Compact market/analysis status panels."""
from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from pa_agent.data.market_status import (
    AnalysisStage,
    AnalysisStatus,
    DataStatusLevel,
    MarketDataStatus,
)


class StatusPanel(QFrame):
    """A lightweight multiline status panel."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self._title = QLabel(title)
        self._title.setObjectName("statusPanelTitle")
        self._body = QLabel("—")
        self._body.setObjectName("statusPanelBody")
        self._body.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._body)

    def set_market_status(self, status: MarketDataStatus) -> None:
        self.setProperty("level", status.level.value)
        latest = status.latest_bar_time.strftime("%Y-%m-%d %H:%M") if status.latest_bar_time else "—"
        success = status.last_success_at.strftime("%H:%M:%S") if status.last_success_at else "—"
        bits = [
            f"状态：{status.message or status.level.value}",
            f"输入：{status.raw_input or '—'}",
            f"解析：{status.resolved_symbol or '—'}",
            f"市场/类型：{status.market or '—'} / {status.asset_type or '—'}",
            f"周期/K线数：{status.timeframe or '—'} / {status.bar_count or '—'}",
            f"数据源：{status.provider or '—'}",
            f"返回：{status.bars_returned if status.bars_returned is not None else '—'} 根，最新：{latest}",
            f"耗时：{status.latency_ms if status.latency_ms is not None else '—'} ms，最近成功：{success}",
        ]
        if status.warning:
            bits.append(f"提示：{status.warning}")
        if status.last_error:
            bits.append(f"最近错误：{status.last_error}")
        self._body.setText("；".join(bits))
        self._refresh_style()

    def set_analysis_status(self, status: AnalysisStatus) -> None:
        level = _analysis_level(status.stage)
        self.setProperty("level", level)
        bits = [
            f"状态：{status.message}",
            f"标的：{status.symbol or '—'}",
            f"周期：{status.timeframe or '—'}",
            f"K线数：{status.bars_count if status.bars_count is not None else '—'}",
            f"来源：{status.provider or '—'}",
        ]
        if status.latency_ms is not None:
            bits.append(f"耗时：{status.latency_ms} ms")
        if status.last_error:
            bits.append(f"最近错误：{status.last_error}")
        self._body.setText("；".join(bits))
        self._refresh_style()

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


def _analysis_level(stage: AnalysisStage) -> str:
    if stage in (AnalysisStage.SUCCESS,):
        return DataStatusLevel.SUCCESS.value
    if stage in (AnalysisStage.WARNING,):
        return DataStatusLevel.WARNING.value
    if stage in (AnalysisStage.ERROR,):
        return DataStatusLevel.ERROR.value
    if stage == AnalysisStage.IDLE:
        return DataStatusLevel.IDLE.value
    return DataStatusLevel.FETCHING.value


__all__ = ["StatusPanel"]
