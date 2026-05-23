"""DecisionPanel — trading decision + market diagnosis summary."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from typing import Any

from pa_agent.util.trade_metrics import (
    compute_risk_reward,
    format_estimated_win_rate,
    min_risk_reward_ratio,
    passes_trader_equation,
)

from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_NO_ORDER = "不下单"

_A_SHARE_ACTION_ZH: dict[str, str] = {
    "analysis_only": "结构分析",
    "long_watch": "观察买点",
    "long_plan": "条件买入计划",
    "risk_warning": "风险/减仓提示",
    "no_action": "无操作",
}

_A_SHARE_ACTION_COLOR: dict[str, str] = {
    "analysis_only": "#8b949e",
    "long_watch": "#58a6ff",
    "long_plan": "#3fb950",
    "risk_warning": "#f85149",
    "no_action": "#8b949e",
}

# Brooks cycle_position → 中文（市场周期 / 频谱位置）
_CYCLE_POSITION_ZH: dict[str, str] = {
    "spike": "尖峰 (Spike)",
    "micro_channel": "微型通道",
    "tight_channel": "窄通道",
    "normal_channel": "正常通道",
    "broad_channel": "宽通道",
    "trending_tr": "趋势型交易区间",
    "trading_range": "交易区间",
    "extreme_tr": "极端交易区间",
    "unknown": "未知",
}

# 以震荡为主的周期类型
_RANGE_CYCLES = frozenset({"trading_range", "extreme_tr", "trending_tr"})

_MARKET_PHASE_ZH: dict[str, str] = {
    "stable": "稳定",
    "transitioning": "过渡",
}


def _format_cycle_position(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _CYCLE_POSITION_ZH.get(key, raw or "—")


def _format_market_phase(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _MARKET_PHASE_ZH.get(key, raw or "—")


def _infer_trend_label(direction: str, cycle_position: str) -> str:
    """Map AI direction + cycle to 上涨 / 下跌 / 震荡."""
    cp = (cycle_position or "").strip().lower()
    d = (direction or "").strip().lower()

    if cp in _RANGE_CYCLES:
        return "震荡"

    if d == "bullish":
        return "上涨"
    if d == "bearish":
        return "下跌"
    if d == "neutral":
        return "震荡"

    # 通道类但未给方向时，根据周期名粗略判断
    if cp in ("spike", "micro_channel", "tight_channel"):
        return "趋势运行中"
    return "—"


def _trend_color(label: str) -> str:
    if label == "上涨":
        return "#3fb950"
    if label == "下跌":
        return "#f85149"
    if label in ("震荡", "趋势运行中"):
        return "#e6b800"
    return "#8b949e"


def _score_color(score: int) -> str:
    if score >= 70:
        return "#3fb950"
    if score >= 50:
        return "#e6b800"
    return "#f85149"


def _parse_score_100(value: object) -> int | None:
    """Parse 0–100 confidence score."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    try:
        return max(0, min(100, int(float(str(value).strip()))))
    except (ValueError, TypeError):
        return None


def _format_watch_price(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.5g}"
    except (TypeError, ValueError):
        return str(value)


class DecisionPanel(QWidget):
    """Renders market diagnosis + Stage-2 trading decision.

    Confidence layout (two bars):
      市场诊断区 → 市场判断置信度 (Stage 2 diagnosis_confidence)
      交易决策区 → 交易决策置信度 (Stage 2 trade_confidence)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("AI 交易决策")
        title.setObjectName("toolbarTitle")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── 市场诊断 ──────────────────────────────────────────────────────
        diag_title = QLabel("市场诊断")
        diag_title.setStyleSheet("font-weight: bold; color: #58a6ff;")
        layout.addWidget(diag_title)

        self._trend_label = QLabel("趋势判断：—")
        self._trend_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._trend_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; padding: 10px;"
            "background-color: #21262d; border-radius: 8px; color: #8b949e;"
        )
        layout.addWidget(self._trend_label)

        self._cycle_label = QLabel("市场周期：—")
        self._cycle_label.setWordWrap(True)
        self._cycle_label.setObjectName("mutedLabel")
        layout.addWidget(self._cycle_label)

        self._phase_label = QLabel("市场阶段：—")
        self._phase_label.setObjectName("mutedLabel")
        layout.addWidget(self._phase_label)

        # ── 市场判断置信度（来自 Stage 2 diagnosis_confidence）───────────
        self._diag_conf_title = QLabel("市场判断置信度")
        self._diag_conf_title.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(self._diag_conf_title)

        self._diag_conf_bar = QProgressBar()
        self._diag_conf_bar.setRange(0, 100)
        self._diag_conf_bar.setTextVisible(True)
        self._diag_conf_bar.setFormat("%v / 100")
        self._diag_conf_bar.setMaximumHeight(22)
        layout.addWidget(self._diag_conf_bar)

        self._diag_conf_label = QLabel("—")
        layout.addWidget(self._diag_conf_label)

        self._diag_reasoning_label = QLabel()
        self._diag_reasoning_label.setWordWrap(True)
        self._diag_reasoning_label.setObjectName("mutedLabel")
        layout.addWidget(self._diag_reasoning_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        # ── 交易决策 ──────────────────────────────────────────────────────
        trade_title = QLabel("交易决策")
        trade_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(trade_title)

        self._conclusion_bar = QFrame()
        self._conclusion_bar.setObjectName("conclusionBar")
        bar_layout = QHBoxLayout(self._conclusion_bar)
        bar_layout.setContentsMargins(14, 12, 14, 12)
        bar_layout.setSpacing(8)

        self._rr_inline_label = QLabel("—")
        self._rr_inline_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._rr_inline_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #58a6ff;"
        )

        self._conclusion_label = QLabel("—")
        self._conclusion_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._conclusion_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        self._win_rate_inline_label = QLabel("—")
        self._win_rate_inline_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._win_rate_inline_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #a371f7;"
        )

        bar_layout.addWidget(self._rr_inline_label, stretch=1)
        bar_layout.addWidget(self._conclusion_label, stretch=2)
        bar_layout.addWidget(self._win_rate_inline_label, stretch=1)
        layout.addWidget(self._conclusion_bar)

        self._details_widget = QWidget()
        details_layout = QVBoxLayout(self._details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(6)

        self._direction_label = QLabel()
        self._order_type_label = QLabel()
        self._entry_label = QLabel()
        self._tp_label = QLabel()
        self._sl_label = QLabel()

        for lbl in (
            self._direction_label,
            self._order_type_label,
            self._entry_label,
            self._tp_label,
            self._sl_label,
        ):
            lbl.setWordWrap(True)
            lbl.setObjectName("mutedLabel")
            details_layout.addWidget(lbl)

        layout.addWidget(self._details_widget)

        # ── A股观察位 ─────────────────────────────────────────────────────
        self._a_share_widget = QWidget()
        a_share_layout = QVBoxLayout(self._a_share_widget)
        a_share_layout.setContentsMargins(0, 2, 0, 2)
        a_share_layout.setSpacing(5)

        a_share_title = QLabel("A股观察位")
        a_share_title.setStyleSheet("font-weight: bold; color: #58a6ff;")
        a_share_layout.addWidget(a_share_title)

        self._a_share_action_label = QLabel()
        self._a_share_levels_label = QLabel()
        self._a_share_position_label = QLabel()
        self._a_share_constraints_label = QLabel()
        for lbl in (
            self._a_share_action_label,
            self._a_share_levels_label,
            self._a_share_position_label,
            self._a_share_constraints_label,
        ):
            lbl.setWordWrap(True)
            lbl.setObjectName("mutedLabel")
            a_share_layout.addWidget(lbl)

        layout.addWidget(self._a_share_widget)

        # ── 交易决策置信度（来自 Stage 2 trade_confidence）────────────────
        self._trade_conf_title = QLabel("交易决策置信度")
        self._trade_conf_title.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(self._trade_conf_title)

        self._trade_conf_bar = QProgressBar()
        self._trade_conf_bar.setRange(0, 100)
        self._trade_conf_bar.setTextVisible(True)
        self._trade_conf_bar.setFormat("%v / 100")
        self._trade_conf_bar.setMaximumHeight(22)
        layout.addWidget(self._trade_conf_bar)

        self._trade_conf_label = QLabel()
        layout.addWidget(self._trade_conf_label)

        self._trade_reasoning_label = QLabel()
        self._trade_reasoning_label.setWordWrap(True)
        self._trade_reasoning_label.setObjectName("mutedLabel")
        layout.addWidget(self._trade_reasoning_label)

        reasoning_title = QLabel("分析理由")
        reasoning_title.setStyleSheet("font-weight: bold; color: #a371f7;")
        layout.addWidget(reasoning_title)

        self._reasoning_edit = QTextEdit()
        self._reasoning_edit.setReadOnly(True)
        self._reasoning_edit.setObjectName("answerPane")
        self._reasoning_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._reasoning_edit, stretch=1)

        self.clear()

    # ── Data binding helpers ──────────────────────────────────────────────

    def _apply_market_diagnosis(
        self,
        diagnosis_summary: dict | None,
        stage1_diagnosis: dict | None = None,
    ) -> None:
        """Fill trend / cycle / phase from stage2 summary, fallback to stage1."""
        src: dict = {}
        if diagnosis_summary:
            src.update(diagnosis_summary)
        if stage1_diagnosis:
            for k, v in stage1_diagnosis.items():
                src.setdefault(k, v)

        direction = str(src.get("direction", "") or "")
        cycle_position = str(src.get("cycle_position", "") or "")
        alt_cycle = src.get("alternative_cycle_position")
        market_phase = str(src.get("market_phase", "") or "")

        trend = _infer_trend_label(direction, cycle_position)
        color = _trend_color(trend)
        self._trend_label.setText(f"趋势判断：{trend}")
        self._trend_label.setStyleSheet(
            f"font-size: 20px; font-weight: bold; padding: 10px;"
            f"color: {color}; background-color: #21262d; border-radius: 8px;"
        )

        cycle_zh = _format_cycle_position(cycle_position)
        cycle_line = f"市场周期：{cycle_zh}"
        if alt_cycle:
            cycle_line += f"（备选：{_format_cycle_position(str(alt_cycle))}）"
        self._cycle_label.setText(cycle_line)

        if market_phase:
            phase_zh = _format_market_phase(market_phase)
            extra = ""
            risk = src.get("transition_risk")
            if market_phase == "transitioning" and risk:
                extra = f" · 过渡风险 {risk}"
            self._phase_label.setText(f"市场阶段：{phase_zh}{extra}")
            self._phase_label.setVisible(True)
        else:
            self._phase_label.setVisible(False)

    def _apply_diagnosis_confidence(
        self,
        diagnosis_confidence: object,
        diagnosis_confidence_reasoning: str | None,
    ) -> None:
        """Render market-judgment confidence bar (Stage 2 diagnosis_confidence)."""
        score = _parse_score_100(diagnosis_confidence)
        if score is not None:
            c_color = _score_color(score)
            self._diag_conf_bar.setValue(score)
            self._diag_conf_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {c_color}; }}"
            )
            self._diag_conf_label.setText(f"评分 {score} / 100")
            self._diag_conf_label.setStyleSheet(f"color: {c_color}; font-weight: bold;")
            reason_text = str(diagnosis_confidence_reasoning or "").strip()
            self._diag_reasoning_label.setText(
                f"理由：{reason_text}" if reason_text else ""
            )
            self._diag_conf_title.setVisible(True)
            self._diag_conf_bar.setVisible(True)
            self._diag_conf_label.setVisible(True)
            self._diag_reasoning_label.setVisible(bool(reason_text))
        else:
            self._diag_conf_bar.setValue(0)
            self._diag_conf_title.setVisible(False)
            self._diag_conf_bar.setVisible(False)
            self._diag_conf_label.setVisible(False)
            self._diag_reasoning_label.setVisible(False)

    def _apply_trade_confidence(
        self,
        trade_confidence: object,
        trade_confidence_reasoning: str | None,
        *,
        no_order: bool = False,
    ) -> None:
        """Render trade-decision confidence bar (Stage 2 trade_confidence)."""
        score = _parse_score_100(trade_confidence)
        if score is not None:
            c_color = _score_color(score)
            hint = "观望决策" if no_order else "入场决策"
            self._trade_conf_bar.setValue(score)
            self._trade_conf_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {c_color}; }}"
            )
            self._trade_conf_label.setText(f"评分 {score} / 100 · {hint}")
            self._trade_conf_label.setStyleSheet(f"color: {c_color}; font-weight: bold;")
            reason_text = str(trade_confidence_reasoning or "").strip()
            self._trade_reasoning_label.setText(
                f"理由：{reason_text}" if reason_text else ""
            )
            self._trade_conf_title.setVisible(True)
            self._trade_conf_bar.setVisible(True)
            self._trade_conf_label.setVisible(True)
            self._trade_reasoning_label.setVisible(bool(reason_text))
        else:
            self._trade_conf_bar.setValue(0)
            self._trade_conf_title.setVisible(False)
            self._trade_conf_bar.setVisible(False)
            self._trade_conf_label.setVisible(False)
            self._trade_reasoning_label.setVisible(False)

    def _set_conclusion_bar_style(self, *, center_color: str) -> None:
        self._conclusion_bar.setStyleSheet(
            "QFrame#conclusionBar {"
            "  background-color: #21262d;"
            "  border-radius: 8px;"
            "}"
        )
        self._conclusion_label.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {center_color};"
        )

    def _reset_conclusion_bar_side_labels(self) -> None:
        self._rr_inline_label.setText("—")
        self._win_rate_inline_label.setText("—")
        self._rr_inline_label.setVisible(False)
        self._win_rate_inline_label.setVisible(False)

    def _apply_a_share(self, a_share: dict | None) -> tuple[str | None, str]:
        """Render A-share observation levels and return (action, label)."""
        if not isinstance(a_share, dict):
            self._a_share_widget.setVisible(False)
            return None, ""

        action = str(a_share.get("action_type") or "").strip()
        action_label = _A_SHARE_ACTION_ZH.get(action, action or "—")
        self._a_share_action_label.setText(f"动作类型：{action_label}")

        levels = a_share.get("watch_levels") or []
        lines: list[str] = []
        if isinstance(levels, list):
            for item in levels:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "观察位").strip()
                price = _format_watch_price(item.get("price"))
                usage = str(item.get("usage") or "").strip()
                basis = str(item.get("basis") or "").strip()
                tail = f"（{basis}）" if basis else ""
                if usage:
                    lines.append(f"{name}: {price} - {usage}{tail}")
                else:
                    lines.append(f"{name}: {price}{tail}")
        self._a_share_levels_label.setText(
            "观察位：\n" + "\n".join(lines) if lines else "观察位：—"
        )

        position_note = str(a_share.get("position_note") or "").strip()
        self._a_share_position_label.setText(
            f"持仓说明：{position_note}" if position_note else "持仓说明：—"
        )

        constraints = a_share.get("constraints") or []
        if isinstance(constraints, list):
            constraints_text = "；".join(str(x) for x in constraints if x)
        else:
            constraints_text = str(constraints)
        self._a_share_constraints_label.setText(
            f"约束：{constraints_text}" if constraints_text else "约束：—"
        )
        self._a_share_widget.setVisible(True)
        return action, action_label

    # ── Public API ────────────────────────────────────────────────────────

    def set_decision(
        self,
        decision: dict,
        *,
        diagnosis_summary: dict | None = None,
        stage1_diagnosis: dict | None = None,
        decision_stance: str | None = None,
        a_share: dict | None = None,
    ) -> None:
        self._apply_market_diagnosis(diagnosis_summary, stage1_diagnosis)
        a_share_action, a_share_label = self._apply_a_share(a_share)

        order_type = decision.get("order_type", _NO_ORDER)
        reasoning = decision.get("reasoning", decision.get("brief_reasoning", ""))
        diag_conf = decision.get("diagnosis_confidence", None)
        diag_conf_reasoning = decision.get("diagnosis_confidence_reasoning", None)
        trade_conf = decision.get("trade_confidence", None)
        trade_conf_reasoning = decision.get("trade_confidence_reasoning", None)

        self._apply_diagnosis_confidence(diag_conf, diag_conf_reasoning)

        if order_type == _NO_ORDER:
            self._reset_conclusion_bar_side_labels()
            self._conclusion_label.setText(a_share_label or "不下单")
            self._set_conclusion_bar_style(
                center_color=_A_SHARE_ACTION_COLOR.get(a_share_action or "", "#8b949e")
            )
            self._details_widget.setVisible(False)
            self._apply_trade_confidence(
                trade_conf, trade_conf_reasoning,
                no_order=True,
            )
        else:
            direction = decision.get("order_direction", "—")
            entry = decision.get("entry_price")
            tp = decision.get("take_profit_price")
            sl = decision.get("stop_loss_price")

            self._conclusion_label.setText(order_type)
            color = "#3fb950" if "多" in str(direction) else "#f85149"
            self._set_conclusion_bar_style(center_color=color)

            rr = compute_risk_reward(entry, tp, sl, direction)
            if rr is not None:
                ratio = float(rr["ratio"])
                risk = float(rr["risk"])
                reward = float(rr["reward"])
                win_pct = _parse_score_100(decision.get("estimated_win_rate"))
                eq_ok = (
                    win_pct is not None
                    and passes_trader_equation(win_pct, risk, reward)
                )
                min_rr = min_risk_reward_ratio(decision_stance)
                metrics_ok = ratio >= min_rr and (eq_ok if win_pct is not None else True)
                eq_note = ""
                if win_pct is not None:
                    eq_note = " · 方程通过" if eq_ok else " · 方程不通过"
                self._rr_inline_label.setText(
                    f"盈亏比  {rr['ratio_text']}（风险 {risk:.4g} / 回报 {reward:.4g}）{eq_note}"
                )
                rr_color = "#3fb950" if metrics_ok else "#f85149"
                self._rr_inline_label.setStyleSheet(
                    f"color: {rr_color}; font-size: 15px; font-weight: bold;"
                )
                self._rr_inline_label.setVisible(True)
            else:
                self._rr_inline_label.setText("盈亏比  —（三价无效）")
                self._rr_inline_label.setStyleSheet(
                    "color: #f85149; font-size: 15px; font-weight: bold;"
                )
                self._rr_inline_label.setVisible(True)

            win_rate = format_estimated_win_rate(decision)
            if win_rate:
                self._win_rate_inline_label.setText(f"预估胜率  {win_rate}")
                self._win_rate_inline_label.setVisible(True)
            else:
                self._win_rate_inline_label.setText("预估胜率  —")
                self._win_rate_inline_label.setVisible(True)

            self._direction_label.setText(f"方向  {direction}")
            self._order_type_label.setText(f"类型  {order_type}")
            self._entry_label.setText(
                f"入场  {entry:.5g}" if entry is not None else "入场  —"
            )
            self._tp_label.setText(f"止盈  {tp:.5g}" if tp is not None else "止盈  —")
            self._sl_label.setText(f"止损  {sl:.5g}" if sl is not None else "止损  —")

            self._details_widget.setVisible(True)
            self._apply_trade_confidence(
                trade_conf, trade_conf_reasoning,
                no_order=False,
            )

        self._reasoning_edit.setPlainText(str(reasoning) if reasoning else "")

    def clear(self) -> None:
        self._trend_label.setText("趋势判断：—")
        self._trend_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; padding: 10px;"
            "color: #6e7681; background-color: #21262d; border-radius: 8px;"
        )
        self._cycle_label.setText("市场周期：—")
        self._phase_label.setText("市场阶段：—")
        self._phase_label.setVisible(True)

        self._diag_conf_bar.setValue(0)
        self._diag_conf_title.setVisible(False)
        self._diag_conf_bar.setVisible(False)
        self._diag_conf_label.setVisible(False)
        self._diag_reasoning_label.setVisible(False)

        self._reset_conclusion_bar_side_labels()
        self._conclusion_label.setText("等待分析")
        self._set_conclusion_bar_style(center_color="#6e7681")
        self._conclusion_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #6e7681;"
        )
        self._details_widget.setVisible(False)
        self._a_share_widget.setVisible(False)

        self._trade_conf_bar.setValue(0)
        self._trade_conf_title.setVisible(False)
        self._trade_conf_bar.setVisible(False)
        self._trade_conf_label.setVisible(False)
        self._trade_reasoning_label.setVisible(False)
        self._reasoning_edit.clear()
