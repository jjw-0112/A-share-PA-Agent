"""Sequence number label item for pyqtgraph."""
from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QFont


class SeqLabelItem(pg.TextItem):
    """A small text label showing a candle's sequence number.

    The label is positioned above the candle's high price.

    Parameters
    ----------
    seq:
        Sequence number (1 = newest bar).
    x_pos:
        Integer x-axis position matching the corresponding CandleItem.
    y_pos:
        Y-axis position (typically the bar's high price).
    """

    _COLOR = QColor(180, 180, 180)  # light grey — unobtrusive

    def __init__(self, seq: int, x_pos: int, y_pos: float, *, font_pt: int = 7) -> None:
        self.seq = int(seq)
        self.x_pos = int(x_pos)
        super().__init__(
            text=f"#{seq}",
            color=self._COLOR,
            anchor=(0.5, 1.0),  # horizontally centred, bottom of text at y_pos
        )
        self.setFont(QFont("Arial", font_pt))
        self.setPos(QPointF(float(x_pos), y_pos))
