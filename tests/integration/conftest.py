"""Shared test infrastructure for TwoStageOrchestrator integration tests."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pa_agent.data.base import KlineBar, KlineFrame, IndicatorBundle
# ── Valid JSON payloads ───────────────────────────────────────────────────────

SAMPLE_GATE_TRACE = [
    {
        "node_id": "0.1",
        "question": "是否看得懂当前市场？",
        "answer": "是",
        "action": "继续",
        "reason": "结构清晰",
        "section": "总原则",
        "bar_range": "K100-K1",
    },
    {
        "node_id": "1.2",
        "question": "是否能识别市场周期？",
        "answer": "是",
        "action": "继续",
        "reason": "normal_channel",
        "section": "是否可以决策",
        "bar_range": "K50-K1",
    },
    {
        "node_id": "2.3",
        "question": "当前方向是多头还是空头？",
        "answer": "是",
        "action": "继续",
        "reason": "多头",
        "branch": "bull",
        "section": "多空方向判断",
        "bar_range": "K30-K1",
    },
]

SAMPLE_DECISION_TRACE = [
    {
        "node_id": "4.1",
        "section": "通道",
        "question": "是否出现有序波段结构？",
        "answer": "是",
        "reason": "HH+HL",
        "skipped": False,
        "bar_range": "K50-K1",
    },
    {
        "node_id": "9.2",
        "section": "入场信号",
        "question": "信号K线方向是否与计划方向一致？",
        "answer": "是",
        "reason": "阳线",
        "skipped": False,
        "bar_range": "K1",
    },
    {
        "node_id": "10.1",
        "section": "风险收益",
        "question": "是否能明确止损？",
        "answer": "是",
        "reason": "信号棒低点外",
        "skipped": False,
        "bar_range": "K1",
    },
    {
        "node_id": "10.2",
        "section": "风险收益",
        "question": "止损是否过大？",
        "answer": "否",
        "reason": "止损合理",
        "skipped": False,
        "bar_range": "K30-K1",
    },
    {
        "node_id": "10.3",
        "section": "风险收益",
        "question": "交易者方程是否通过？",
        "answer": "是",
        "reason": "RR 约 2:1",
        "skipped": False,
        "bar_range": "K1",
    },
    {
        "node_id": "11.2",
        "section": "下单方式",
        "question": "是通道回撤吗？",
        "answer": "是",
        "reason": "回撤限价",
        "skipped": False,
        "bar_range": "K20-K1",
    },
]

VALID_STAGE1 = {
    "cycle_position": "normal_channel",
    "direction": "bullish",
    "diagnosis_confidence": 75,
    "market_phase": "stable",
    "detected_patterns": [],
    "key_signals": ["signal1"],
    "htf_context": "bullish trend",
    "entry_setup": "buy on pullback",
    "strategy_files_needed": ["上涨通道分析识别.txt"],
    "bar_by_bar_summary": [
        {
            "bar": "K1",
            "role": "confirmation",
            "bar_type": "trend_bull",
            "context_effect": "strengthens_bull",
            "follow_through": "pending",
            "trapped_side": "none",
            "reason": "最新K线延续多头结构",
        }
    ],
    "gate_trace": SAMPLE_GATE_TRACE,
    "gate_result": "proceed",
}

VALID_STAGE2 = {
    "decision": {
        "order_direction": "做多",
        "order_type": "限价单",
        "entry_price": 2000.0,
        "take_profit_price": 2050.0,
        "stop_loss_price": 1980.0,
        "reasoning": "Strong bullish signal",
        "diagnosis_confidence": 75,
        "diagnosis_confidence_reasoning": "周期位置明确，趋势方向清晰",
        "trade_confidence": 70,
        "trade_confidence_reasoning": "入场信号明确，风险回报比合理",
        "estimated_win_rate": 55,
        "estimated_win_rate_reasoning": "结构清晰，方程边际通过，取 55% 用于 10.3",
        "key_factors": ["factor1"],
        "watch_points": ["watch1"],
        "risk_assessment": "low risk",
        "invalidation_condition": "break below 1980",
    },
    "a_share": {
        "action_type": "long_plan",
        "watch_levels": [
            {
                "name": "突破触发",
                "price": 2000.0,
                "basis": "K2 高点突破后回踩确认",
                "usage": "满足条件后才考虑做多计划",
            }
        ],
        "position_note": "未提供持仓信息，减仓/防守仅适用于已有持仓者",
        "constraints": ["不输出开空计划", "不要求用户输入持仓", "不计算仓位数量"],
    },
    "diagnosis_summary": {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "key_signals": ["signal1"],
    },
    "bar_analysis": {
        "always_in": "long",
        "last_closed_bar": "K1",
        "bar_type": "trend_bull",
        "signal_bar": {
            "bar": "K2",
            "quality": "strong",
            "pattern": "H1",
            "reason": "信号棒质量较好",
        },
        "entry_bar": {
            "bar": "K1",
            "strength": "strong",
            "follow_through": True,
            "still_valid": True,
            "freshness": "fresh",
        },
        "second_entry": {
            "is_second_entry": False,
            "type": "none",
        },
    },
    "decision_trace": SAMPLE_DECISION_TRACE,
    "terminal": {
        "node_id": "11.2",
        "outcome": "trade",
        "label": "10.3 已通过，通道回撤限价入场",
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_reply(content_dict: dict) -> MagicMock:
    """Build a mock AIReply from a content dict."""
    reply = MagicMock()
    reply.content = json.dumps(content_dict, ensure_ascii=False)
    reply.reasoning_content = ""
    reply.raw = {"content": reply.content}
    reply.latency_ms = 1.0
    reply.usage = MagicMock()
    reply.usage.prompt_tokens = 100
    reply.usage.completion_tokens = 50
    reply.usage.cached_prompt_tokens = 0
    reply.usage.total_tokens = 150
    return reply


def make_frame() -> KlineFrame:
    """Build a minimal KlineFrame for testing."""
    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=1000 - i * 60000,
            open=2000.0,
            high=2010.0,
            low=1990.0,
            close=2005.0,
            volume=100.0,
            closed=(i > 0),
        )
        for i in range(5)
    )
    indicators = IndicatorBundle(
        ema20=tuple([2000.0] * 5),
        atr14=tuple([10.0] * 5),
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=bars,
        snapshot_ts_local_ms=1700000000000,
        indicators=indicators,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def frame():
    return make_frame()


@pytest.fixture
def pending_writer():
    return MagicMock()


@pytest.fixture
def assembler():
    mock = MagicMock()
    mock.build_stage1.return_value = [{"role": "system", "content": "test"}]
    mock.build_stage2.return_value = [{"role": "system", "content": "test"}]
    return mock


@pytest.fixture
def exp_reader():
    mock = MagicMock()
    mock.read_top5.return_value = []
    return mock
