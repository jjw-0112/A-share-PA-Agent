"""QPushButton with task-bound visual states."""
from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QPushButton


class ButtonVisualState(str, Enum):
    DEFAULT = "default"
    LOADING = "loading"
    SUCCESS = "success"
    ERROR = "error"
    DISABLED = "disabled"


class ActionButton(QPushButton):
    """Small convenience wrapper for long-running actions."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._default_text = text
        self.setProperty("state", ButtonVisualState.DEFAULT.value)

    def set_loading(self, text: str | None = None) -> None:
        self._set_state(ButtonVisualState.LOADING, text, enabled=False)

    def set_success(self, text: str | None = None, *, reset_after_ms: int | None = 1200) -> None:
        self._set_state(ButtonVisualState.SUCCESS, text, enabled=True)
        if reset_after_ms is not None:
            QTimer.singleShot(reset_after_ms, lambda: self.reset_default())

    def set_error(self, text: str | None = None, *, reset_after_ms: int | None = 1800) -> None:
        self._set_state(ButtonVisualState.ERROR, text, enabled=True)
        if reset_after_ms is not None:
            QTimer.singleShot(reset_after_ms, lambda: self.reset_default())

    def reset_default(self, text: str | None = None) -> None:
        self._set_state(ButtonVisualState.DEFAULT, text or self._default_text, enabled=True)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt API
        super().setEnabled(enabled)
        if not enabled and self.property("state") != ButtonVisualState.LOADING.value:
            self.setProperty("state", ButtonVisualState.DISABLED.value)
            self._refresh_style()
        elif enabled and self.property("state") == ButtonVisualState.DISABLED.value:
            self.setProperty("state", ButtonVisualState.DEFAULT.value)
            self._refresh_style()

    def _set_state(
        self,
        state: ButtonVisualState,
        text: str | None,
        *,
        enabled: bool,
    ) -> None:
        if self.property("state") == ButtonVisualState.DEFAULT.value:
            self._default_text = self.text() or self._default_text
        if text:
            self.setText(text)
        super().setEnabled(enabled)
        self.setProperty("state", state.value)
        self._refresh_style()

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


__all__ = ["ActionButton", "ButtonVisualState"]
