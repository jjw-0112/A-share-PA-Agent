"""Self-drawn candlestick item for pyqtgraph."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PyQt6.QtCore import QLineF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QPicture

if TYPE_CHECKING:
    from pa_agent.data.base import KlineBar

# Candle colors
# close >= open → price went UP → green
# close <  open → price went DOWN → red
_COLOR_UP   = QColor(38, 166, 154)   # #26a69a  teal-green
_COLOR_DOWN = QColor(239, 83, 80)    # #ef5350  red

# Candle body width as a fraction of the x-spacing (0..1)
_BODY_WIDTH = 0.6


class CandleItem(pg.GraphicsObject):
    """A single OHLCV candlestick drawn via QPainter.

    Parameters
    ----------
    bar:
        The KlineBar data for this candle.
    x_pos:
        Integer x-axis position (0 = leftmost / oldest visible candle).
    """

    def __init__(self, bar: "KlineBar", x_pos: float, *, body_width: float = _BODY_WIDTH) -> None:
        super().__init__()
        self._bar = bar
        self._x = float(x_pos)
        self._body_width = max(0.25, min(0.85, float(body_width)))
        # close >= open → bullish (green UP); close < open → bearish (red DOWN)
        self._color = _COLOR_UP if bar.close >= bar.open else _COLOR_DOWN
        self._generate_picture()

    # ── pyqtgraph interface ───────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        half = self._body_width / 2.0
        top = self._bar.high
        bottom = self._bar.low
        # Add a small margin so the wick tip is not clipped
        span = top - bottom
        margin = span * 0.05 + 1e-8
        return QRectF(
            self._x - half,
            bottom - margin,
            self._body_width,
            span + 2 * margin,
        )

    def paint(
        self,
        painter: QPainter,
        option: object,  # QStyleOptionGraphicsItem
        widget: object = None,
    ) -> None:
        painter.drawPicture(0, 0, self._picture)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _generate_picture(self) -> None:
        """Pre-render the candle into a QPicture for fast repaints."""
        self._picture = QPicture()
        p = QPainter(self._picture)
        p.setPen(QPen(self._color, 0))  # width=0 → cosmetic (1px)
        p.setBrush(self._color)

        bar = self._bar
        x = float(self._x)
        width = self._body_width
        half = width / 2.0

        body_top = max(bar.open, bar.close)
        body_bottom = min(bar.open, bar.close)

        # Body rectangle (ensure non-zero height for doji candles)
        body_height = max(body_top - body_bottom, 1e-8)
        p.drawRect(QRectF(x - half, body_bottom, width, body_height))

        # Upper wick
        if bar.high > body_top:
            p.drawLine(QLineF(x, body_top, x, bar.high))

        # Lower wick
        if bar.low < body_bottom:
            p.drawLine(QLineF(x, body_bottom, x, bar.low))

        p.end()
