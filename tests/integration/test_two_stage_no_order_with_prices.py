"""Integration test: stage 2 has order_type='不下单' but entry_price is non-null.

Task 11.8
"""
from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, make_reply


def _make_reply(content_dict: dict) -> MagicMock:
    reply = MagicMock()
    reply.content = json.dumps(content_dict)
    reply.reasoning_content = ""
    reply.raw = {"content": reply.content}
    reply.latency_ms = 1.0
    reply.usage = MagicMock()
    reply.usage.prompt_tokens = 100
    reply.usage.completion_tokens = 50
    reply.usage.cached_prompt_tokens = 0
    reply.usage.total_tokens = 150
    return reply


# Stage 2 with 不下单 but entry_price=0 (should be null per iron law)
NO_ORDER_WITH_PRICE = {
    "decision": {
        "order_direction": None,
        "order_type": "不下单",
        "entry_price": 0,          # violates iron law — must be null
        "take_profit_price": None,
        "stop_loss_price": None,
        "reasoning": "Market unclear",
        "diagnosis_confidence": 40,
        "diagnosis_confidence_reasoning": "周期位置存在歧义",
        "trade_confidence": 30,
        "trade_confidence_reasoning": "缺乏明确入场信号",
        "estimated_win_rate": None,
        "estimated_win_rate_reasoning": "未下单",
        "key_factors": ["factor1"],
        "watch_points": ["watch1"],
        "risk_assessment": "high risk",
        "invalidation_condition": "n/a",
    },
    "a_share": {
        "action_type": "no_action",
        "watch_levels": [],
        "position_note": "未提供持仓信息，减仓/防守仅适用于已有持仓者",
        "constraints": ["不输出开空计划", "不要求用户输入持仓", "不计算仓位数量"],
    },
    "diagnosis_summary": {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "key_signals": ["signal1"],
    },
    "decision_trace": [
        {
            "node_id": "9.2",
            "section": "入场",
            "question": "信号方向一致？",
            "answer": "否",
            "reason": "无效",
            "skipped": False,
            "bar_range": "K1",
        },
    ],
    "terminal": {
        "node_id": "10.3",
        "outcome": "reject",
        "label": "不下单但价格字段违规",
    },
}


def test_no_order_with_entry_price(frame, pending_writer, assembler, exp_reader):
    """order_type='不下单' with entry_price=0 → category 'c', count == 1."""
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        _make_reply(NO_ORDER_WITH_PRICE),
    ]

    validator = JsonValidator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []
    cancel_token = CancelToken()

    record = orchestrator.submit(
        frame=frame,
        cancel_token=cancel_token,
        on_event=events.append,
    )

    # Validation error category should be 'c' (invalid value / iron law violation)
    assert record.exception is not None
    assert record.exception["category"] == "c"

    # Stage2 started but failed
    assert OrchestratorEvent.Stage2Started in events
    assert OrchestratorEvent.Stage2Failed in events
    assert OrchestratorEvent.RecordSaved not in events
