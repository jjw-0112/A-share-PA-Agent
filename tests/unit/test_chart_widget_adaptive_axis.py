"""ChartWidget A-share date axis and adaptive range tests."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")


@pytest.fixture
def chart_widget(qtbot):
    from pa_agent.gui.chart_widget import ChartWidget

    widget = ChartWidget()
    widget.resize(1000, 520)
    qtbot.addWidget(widget)
    return widget


def _make_frame(n: int = 120, timeframe: str = "1m"):
    from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame

    if timeframe == "1d":
        delta = timedelta(days=1)
        oldest = datetime(2026, 1, 5, 15, 0)
    else:
        delta = timedelta(minutes=1 if timeframe == "1m" else 5)
        oldest = datetime(2026, 5, 22, 9, 30)

    bars_oldest = []
    for x in range(n):
        ts = oldest + x * delta
        price = 100.0 + x * 0.25
        bars_oldest.append(
            KlineBar(
                seq=n - x,
                ts_open=int(ts.timestamp() * 1000),
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.35,
                volume=10_000 + x,
                closed=True,
            )
        )

    bars_newest = tuple(reversed(bars_oldest))
    ema = tuple(bar.close for bar in bars_newest)
    return KlineFrame(
        symbol="STOCK:600519",
        timeframe=timeframe,
        bars=bars_newest,
        indicators=IndicatorBundle(ema20=ema, atr14=tuple([1.0] * n)),
        snapshot_ts_local_ms=int(datetime(2026, 5, 22, 15, 0).timestamp() * 1000),
    )


def test_intraday_axis_renders_time_labels(chart_widget):
    frame = _make_frame(n=40, timeframe="1m")

    chart_widget.set_frame_now(frame)

    labels = chart_widget._date_axis.tickStrings([0, 10, 39], 1, 1)
    assert any(":" in label for label in labels)
    assert labels != ["0", "10", "39"]


def test_daily_axis_renders_date_labels(chart_widget):
    frame = _make_frame(n=40, timeframe="1d")

    chart_widget.set_frame_now(frame)

    labels = chart_widget._date_axis.tickStrings([0, 20, 39], 1, 1)
    assert any("-" in label for label in labels)
    assert labels != ["0", "20", "39"]


def test_initial_view_range_is_bounded_to_data(chart_widget):
    n = 500
    frame = _make_frame(n=n, timeframe="1m")

    chart_widget.set_frame_now(frame)

    x_min, x_max = chart_widget.viewRange()[0]
    y_min, y_max = chart_widget.viewRange()[1]
    lows = [bar.low for bar in chart_widget._bars_oldest_first]
    highs = [bar.high for bar in chart_widget._bars_oldest_first]

    assert -1.5 <= x_min <= 0.0
    assert n - 1 <= x_max <= n + 1.5
    assert y_min < min(lows)
    assert y_max > max(highs)


def test_y_range_tracks_visible_candles_after_horizontal_zoom(chart_widget):
    frame = _make_frame(n=500, timeframe="1m")
    chart_widget.set_frame_now(frame)

    chart_widget.setXRange(100, 120, padding=0)
    chart_widget._apply_y_range_for_x(100, 120)

    y_min, y_max = chart_widget.viewRange()[1]
    visible = chart_widget._bars_oldest_first[100:121]
    all_lows = [bar.low for bar in chart_widget._bars_oldest_first]
    all_highs = [bar.high for bar in chart_widget._bars_oldest_first]

    assert y_min < min(bar.low for bar in visible)
    assert y_max > max(bar.high for bar in visible)
    assert (y_max - y_min) < (max(all_highs) - min(all_lows)) * 0.5


def test_sequence_labels_are_sampled_and_keep_newest(chart_widget):
    chart_widget.set_frame_now(_make_frame(n=500, timeframe="1m"))

    assert len(chart_widget._seq_labels) <= 32
    assert any(label.seq == 1 for label in chart_widget._seq_labels)
