"""A-share consistency validation for Stage 2 JSON."""
from __future__ import annotations

import copy
import json

from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from tests.integration.conftest import VALID_STAGE2


validator = JsonValidator()


def _payload() -> dict:
    return copy.deepcopy(VALID_STAGE2)


def test_long_plan_valid_payload_passes() -> None:
    result = validator.validate("stage2", json.dumps(_payload(), ensure_ascii=False))
    assert isinstance(result, Ok)


def test_a_share_rejects_short_direction() -> None:
    obj = _payload()
    obj["decision"]["order_direction"] = "做空"
    result = validator.validate("stage2", json.dumps(obj, ensure_ascii=False))
    assert isinstance(result, ValidationError)
    assert "decision.order_direction" in result.invalid_fields


def test_risk_warning_cannot_pair_with_order_prices() -> None:
    obj = _payload()
    obj["a_share"]["action_type"] = "risk_warning"
    result = validator.validate("stage2", json.dumps(obj, ensure_ascii=False))
    assert isinstance(result, ValidationError)
    assert "decision.order_type" in result.invalid_fields
    assert "decision.entry_price" in result.invalid_fields


def test_long_plan_requires_watch_levels() -> None:
    obj = _payload()
    obj["a_share"]["watch_levels"] = []
    result = validator.validate("stage2", json.dumps(obj, ensure_ascii=False))
    assert isinstance(result, ValidationError)
    assert "a_share.watch_levels" in result.invalid_fields


def test_constraints_must_include_no_short_plan() -> None:
    obj = _payload()
    obj["a_share"]["constraints"] = ["不计算仓位数量"]
    result = validator.validate("stage2", json.dumps(obj, ensure_ascii=False))
    assert isinstance(result, ValidationError)
    assert "a_share.constraints" in result.invalid_fields
