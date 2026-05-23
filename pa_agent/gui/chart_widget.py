"""ChartWidget - pyqtgraph K-line chart with date axis and adaptive view."""
from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QToolTip

from pa_agent.gui.widgets.candle_item import CandleItem
from pa_agent.gui.widgets.overlay_lines import OverlayLines
from pa_agent.gui.widgets.seq_label_item import SeqLabelItem
from pa_agent.util.trade_metrics import is_long_direction

if TYPE_CHECKING:
    from PyQt6.QtGui import QMouseEvent, QResizeEvent

    from pa_agent.data.base import KlineBar, KlineFrame

# ── Constants ─────────────────────────────────────────────────────────────────

_TIMER_INTERVAL_MS = 33  # ~30 Hz
_EMA_COLOR = (255, 200, 0)  # amber
_NO_ORDER_TEXT = "不下单"
_X_PAD = 0.7
_MIN_VISIBLE_BARS = 6.0
_MAX_SEQ_LABELS = 28


def _timestamp_seconds(ts_open: float) -> float:
    """Normalize mixed project timestamps to Unix seconds."""
    ts = float(ts_open)
    return ts / 1000.0 if abs(ts) > 10_000_000_000 else ts


def _datetime_from_ts(ts_open: float) -> datetime:
    return datetime.fromtimestamp(_timestamp_seconds(ts_open))


def _is_intraday_timeframe(timeframe: str) -> bool:
    return timeframe.endswith("m") or timeframe.endswith("h")


class _KlineDateAxis(pg.AxisItem):
    """Bottom axis that keeps equal-spaced candles but renders real timestamps."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._timestamps: list[float] = []
        self._timeframe: str = ""
        self._cross_day: bool = False
        self._cross_year: bool = False

    def set_mapping(self, timestamps: list[float], timeframe: str) -> None:
        self._timestamps = list(timestamps)
        self._timeframe = timeframe
        dts = [_datetime_from_ts(ts) for ts in self._timestamps]
        self._cross_day = len({dt.date() for dt in dts}) > 1
        self._cross_year = len({dt.year for dt in dts}) > 1

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - pyqtgraph API
        del scale, spacing
        labels: list[str] = []
        seen: set[int] = set()
        for value in values:
            idx = int(round(float(value)))
            if (
                idx in seen
                or idx < 0
                or idx >= len(self._timestamps)
                or abs(float(value) - idx) > 0.35
            ):
                labels.append("")
                continue
            seen.add(idx)
            labels.append(self._format_ts(self._timestamps[idx]))
        return labels

    def _format_ts(self, ts_open: float) -> str:
        dt = _datetime_from_ts(ts_open)
        if _is_intraday_timeframe(self._timeframe):
            return dt.strftime("%m-%d %H:%M") if self._cross_day else dt.strftime("%H:%M")
        if self._timeframe == "1d":
            return dt.strftime("%Y-%m-%d") if self._cross_year else dt.strftime("%m-%d")
        return dt.strftime("%Y-%m-%d")


class ChartWidget(pg.PlotWidget):
    """Interactive K-line chart widget."""

    def __init__(self, parent=None) -> None:
        axis = _KlineDateAxis(orientation="bottom")
        super().__init__(parent=parent, axisItems={"bottom": axis})
        self._date_axis = axis

        # Configure plot appearance.
        self.setBackground("#0d1117")
        self.showGrid(x=False, y=True, alpha=0.3)
        self.getPlotItem().setLabel("left", "价格")
        self.getPlotItem().setLabel("bottom", "时间")
        self.setMouseTracking(True)
        self.setMouseEnabled(x=True, y=False)
        self.setMinimumSize(720, 420)

        # Internal state.
        self._latest_frame: KlineFrame | None = None
        self._dirty: bool = False
        self._candle_items: list[CandleItem] = []
        self._seq_labels: list[SeqLabelItem] = []
        self._ema_line: pg.PlotDataItem | None = None
        self._overlay = OverlayLines()
        self._pending_decision: dict | None = None
        self._direction_items: list[pg.GraphicsItem] = []
        self._seq_label_font_pt: int = 7
        self._bars_oldest_first: list[KlineBar] = []
        self._ema_oldest_first: list[float] = []
        self._suppress_range_callback = False

        view_box = self.getPlotItem().getViewBox()
        view_box.setDefaultPadding(0.0)
        view_box.sigXRangeChanged.connect(self._on_x_range_changed)

        # 30 Hz redraw timer.
        self._timer = QTimer(self)
        self._timer.setInterval(_TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_seq_label_font_pt(self, point_size: int) -> None:
        """Set K-line sequence label font size and refresh the chart if needed."""
        point_size = max(6, min(24, int(point_size)))
        if point_size == self._seq_label_font_pt:
            return
        self._seq_label_font_pt = point_size
        if self._latest_frame is not None:
            self._dirty = True

    def set_frame(self, frame: "KlineFrame") -> None:
        """Cache the latest KlineFrame; actual redraw happens on the timer."""
        self._latest_frame = frame
        self._dirty = True

    def set_frame_now(self, frame: "KlineFrame") -> None:
        """Apply *frame* to the chart immediately (bypass 30 Hz throttle)."""
        self._latest_frame = frame
        self._dirty = False
        self._render_frame(frame)

    def displayed_frame(self) -> "KlineFrame | None":
        """Return the KlineFrame currently shown on the chart."""
        return self._latest_frame

    def fit_to_data(self) -> None:
        """Reset the view to the full K-line range with adaptive Y scaling."""
        n = len(self._bars_oldest_first)
        if n == 0:
            return

        left = -_X_PAD
        right = max(float(n - 1), 0.0) + _X_PAD
        self._apply_x_limits(n)
        self._suppress_range_callback = True
        try:
            self.setXRange(left, right, padding=0)
        finally:
            self._suppress_range_callback = False
        self._apply_y_range_for_x(left, right)

    def set_decision(self, decision: dict) -> None:
        """Draw or clear entry/TP/SL lines and direction marker from the AI decision."""
        self._pending_decision = decision
        order_type = decision.get("order_type", _NO_ORDER_TEXT)
        if order_type == _NO_ORDER_TEXT:
            self._overlay.clear_lines(self)
            self._clear_direction_marker()
            self._pending_decision = None
            return

        entry = decision.get("entry_price")
        tp = decision.get("take_profit_price")
        sl = decision.get("stop_loss_price")

        if entry is not None and tp is not None and sl is not None:
            try:
                self._overlay.set_lines(self, float(entry), float(tp), float(sl))
            except (TypeError, ValueError):
                self._overlay.clear_lines(self)
        else:
            self._overlay.clear_lines(self)

        self._update_direction_marker()
        self._apply_y_range_for_current_x()

    def clear_decision_overlay(self) -> None:
        """Remove entry/TP/SL lines and direction marker; keep the current K-line frame."""
        self._overlay.clear_lines(self)
        self._clear_direction_marker()
        self._pending_decision = None
        self._apply_y_range_for_current_x()

    def reset(self) -> None:
        """Clear all chart items (candles, labels, EMA, overlay lines)."""
        self.clear_decision_overlay()
        self._clear_candles_and_labels()
        if self._ema_line is not None:
            self.removeItem(self._ema_line)
            self._ema_line = None
        self._latest_frame = None
        self._bars_oldest_first = []
        self._ema_oldest_first = []
        self._date_axis.set_mapping([], "")
        self._dirty = False

    # ── Qt events ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if getattr(self, "_latest_frame", None) is not None:
            self._dirty = True

    def mouseDoubleClickEvent(self, event: "QMouseEvent") -> None:  # noqa: N802 - Qt API
        self.fit_to_data()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: "QMouseEvent") -> None:  # noqa: N802 - Qt API
        super().mouseMoveEvent(event)
        self._show_hover_tooltip(event)

    # ── Timer slot ────────────────────────────────────────────────────────────

    def _on_timer(self) -> None:
        """Called every ~33 ms; redraws only when a new frame is available."""
        if not self._dirty or self._latest_frame is None:
            return
        self._dirty = False
        self._render_frame(self._latest_frame)

    # ── Internal rendering ────────────────────────────────────────────────────

    def _render_frame(self, frame: "KlineFrame") -> None:
        """Rebuild all candle items, EMA line, and sequence labels."""
        self._clear_candles_and_labels()
        if self._ema_line is not None:
            self.removeItem(self._ema_line)
            self._ema_line = None

        bars = frame.bars
        n = len(bars)
        if n == 0:
            self._bars_oldest_first = []
            self._ema_oldest_first = []
            self._date_axis.set_mapping([], frame.timeframe)
            return

        self._bars_oldest_first = list(reversed(bars))
        self._ema_oldest_first = list(reversed(frame.indicators.ema20))
        self._date_axis.set_mapping([bar.ts_open for bar in self._bars_oldest_first], frame.timeframe)

        body_width = self._body_width_for_count(n)
        label_step = self._seq_label_step(n)
        label_offset = self._seq_label_y_offset()
        ema_x: list[float] = []
        ema_y: list[float] = []

        for x_pos, bar in enumerate(self._bars_oldest_first):
            candle = CandleItem(bar, float(x_pos), body_width=body_width)
            self.addItem(candle)
            self._candle_items.append(candle)

            if self._should_show_seq_label(bar.seq, n, label_step):
                seq_label = SeqLabelItem(
                    bar.seq,
                    x_pos,
                    bar.high + label_offset,
                    font_pt=self._seq_label_font_pt,
                )
                self.addItem(seq_label)
                self._seq_labels.append(seq_label)

            ema_val = self._ema_oldest_first[x_pos]
            if not math.isnan(ema_val):
                ema_x.append(float(x_pos))
                ema_y.append(float(ema_val))

        if ema_x:
            self._ema_line = pg.PlotDataItem(
                x=np.array(ema_x),
                y=np.array(ema_y),
                pen=pg.mkPen(color=_EMA_COLOR, width=1),
            )
            self.addItem(self._ema_line)

        self._update_direction_marker()
        self.fit_to_data()

    def _clear_direction_marker(self) -> None:
        for item in self._direction_items:
            self.removeItem(item)
        self._direction_items.clear()

    def _update_direction_marker(self) -> None:
        """Draw ▲/▼ at newest bar x entry price for long/short decisions."""
        self._clear_direction_marker()
        decision = self._pending_decision
        frame = self._latest_frame
        if decision is None or frame is None:
            return
        if decision.get("order_type", _NO_ORDER_TEXT) == _NO_ORDER_TEXT:
            return

        entry = decision.get("entry_price")
        if entry is None:
            return
        try:
            entry_f = float(entry)
        except (TypeError, ValueError):
            return

        n = len(frame.bars)
        if n == 0:
            return

        long = is_long_direction(decision.get("order_direction"))
        if long is True:
            symbol, color = "▲", (63, 185, 80)
            anchor = (0.5, 1.0)
        elif long is False:
            symbol, color = "▼", (248, 81, 73)
            anchor = (0.5, 0.0)
        else:
            return

        marker = pg.TextItem(
            text=symbol,
            color=color,
            anchor=anchor,
        )
        from PyQt6.QtGui import QFont

        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        marker.setFont(font)
        marker.setPos(float(n - 1), entry_f)
        self.addItem(marker)
        self._direction_items.append(marker)

    def _clear_candles_and_labels(self) -> None:
        """Remove all candle and label items from the plot."""
        for item in self._candle_items:
            self.removeItem(item)
        self._candle_items.clear()

        for item in self._seq_labels:
            self.removeItem(item)
        self._seq_labels.clear()

    # ── View range helpers ────────────────────────────────────────────────────

    def _apply_x_limits(self, n: int) -> None:
        right = max(float(n - 1), 0.0) + _X_PAD
        max_range = max(_MIN_VISIBLE_BARS, float(n) + 2 * _X_PAD)
        self.getPlotItem().getViewBox().setLimits(
            xMin=-_X_PAD,
            xMax=right,
            minXRange=min(_MIN_VISIBLE_BARS, max_range),
            maxXRange=max_range,
        )

    def _on_x_range_changed(self, *args) -> None:
        del args
        if self._suppress_range_callback:
            return
        self._apply_y_range_for_current_x()

    def _apply_y_range_for_current_x(self) -> None:
        if not self._bars_oldest_first:
            return
        x_min, x_max = self.viewRange()[0]
        self._apply_y_range_for_x(float(x_min), float(x_max))

    def _apply_y_range_for_x(self, x_min: float, x_max: float) -> None:
        visible = self._visible_bar_indices(x_min, x_max)
        if not visible:
            return

        highs = [self._bars_oldest_first[i].high for i in visible]
        lows = [self._bars_oldest_first[i].low for i in visible]
        for i in visible:
            ema = self._ema_oldest_first[i] if i < len(self._ema_oldest_first) else math.nan
            if not math.isnan(ema):
                highs.append(float(ema))
                lows.append(float(ema))

        decision = self._pending_decision or {}
        if decision.get("order_type") != _NO_ORDER_TEXT:
            for key in ("entry_price", "take_profit_price", "stop_loss_price"):
                try:
                    price = float(decision.get(key))
                except (TypeError, ValueError):
                    continue
                highs.append(price)
                lows.append(price)

        low = min(lows)
        high = max(highs)
        span = high - low
        if span <= 0:
            span = max(abs(high) * 0.01, 1.0)
            low -= span / 2
            high += span / 2

        pad = span * self._y_padding_fraction()
        self._suppress_range_callback = True
        try:
            self.setYRange(low - pad, high + pad, padding=0)
        finally:
            self._suppress_range_callback = False

    def _visible_bar_indices(self, x_min: float, x_max: float) -> list[int]:
        n = len(self._bars_oldest_first)
        if n == 0:
            return []
        left = max(0, int(math.floor(min(x_min, x_max))))
        right = min(n - 1, int(math.ceil(max(x_min, x_max))))
        if right < left:
            return []
        return list(range(left, right + 1))

    def _y_padding_fraction(self) -> float:
        width = max(float(self.width()), 1.0)
        height = max(float(self.height()), 1.0)
        aspect = height / width
        return min(0.22, 0.08 + max(0.0, aspect - 0.65) * 0.08)

    # ── Label and candle sizing ───────────────────────────────────────────────

    def _body_width_for_count(self, n: int) -> float:
        if n <= 80:
            return 0.70
        if n <= 300:
            return 0.62
        return 0.52

    def _seq_label_step(self, n: int) -> int:
        width_px = max(self.width(), 1)
        max_labels = max(6, min(_MAX_SEQ_LABELS, width_px // 60))
        return max(1, math.ceil(n / max_labels))

    def _should_show_seq_label(self, seq: int, n: int, step: int) -> bool:
        return seq in (1, n) or (seq - 1) % step == 0

    def _seq_label_y_offset(self) -> float:
        if not self._bars_oldest_first:
            return 0.0
        low = min(bar.low for bar in self._bars_oldest_first)
        high = max(bar.high for bar in self._bars_oldest_first)
        span = high - low
        return max(span * 0.012, abs(high) * 0.0002, 1e-8)

    # ── Hover tooltip ─────────────────────────────────────────────────────────

    def _show_hover_tooltip(self, event: "QMouseEvent") -> None:
        if not self._bars_oldest_first:
            QToolTip.hideText()
            return
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        mouse_point = self.getPlotItem().getViewBox().mapSceneToView(self.mapToScene(pos))
        idx = int(round(mouse_point.x()))
        if idx < 0 or idx >= len(self._bars_oldest_first):
            QToolTip.hideText()
            return

        bar = self._bars_oldest_first[idx]
        dt = _datetime_from_ts(bar.ts_open).strftime("%Y-%m-%d %H:%M")
        text = (
            f"{dt}\n"
            f"K{bar.seq}  {'已收盘' if bar.closed else '形成中'}\n"
            f"开 {bar.open:.4f}  高 {bar.high:.4f}\n"
            f"低 {bar.low:.4f}  收 {bar.close:.4f}\n"
            f"量 {bar.volume:,.0f}"
        )
        try:
            global_pos = event.globalPosition().toPoint()
        except AttributeError:
            global_pos = event.globalPos()
        QToolTip.showText(global_pos, text, self)
