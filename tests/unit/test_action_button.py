"""ActionButton visual state tests."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")


def test_action_button_state_transitions(qtbot) -> None:
    from pa_agent.gui.widgets.action_button import ActionButton

    button = ActionButton("提交分析")
    qtbot.addWidget(button)

    assert button.property("state") == "default"
    assert button.isEnabled()

    button.set_loading("分析中…")
    assert button.property("state") == "loading"
    assert button.text() == "分析中…"
    assert not button.isEnabled()

    button.set_success("分析完成", reset_after_ms=None)
    assert button.property("state") == "success"
    assert button.isEnabled()

    button.set_error("分析失败", reset_after_ms=None)
    assert button.property("state") == "error"
    assert button.isEnabled()

    button.reset_default("提交分析")
    assert button.property("state") == "default"
    assert button.text() == "提交分析"
