"""The four-step strip across the top of the main window: Inspect, Transform, Compare,
Submit. Each step shows whether it's done, a one-line status, and the action that
moves it along, so the workflow is visible without knowing the menus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from photometry_workbench.ui import coloring

DONE, CURRENT, UPCOMING = "done", "current", "upcoming"


@dataclass
class StepState:
    status: str
    """One or two short lines under the step's title."""
    state: str = UPCOMING
    actions: list[tuple[str, Callable[[], None]]] = field(default_factory=list)
    """(button label, callback); the first is the step's main action."""
    enabled: bool = True


class _Step(QFrame):
    def __init__(self, number: int, title: str):
        super().__init__()
        self.setObjectName("step")
        self._title_text = f"{number}  {title}"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self.title = QLabel()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        layout.addWidget(self.title)
        layout.addWidget(self.status)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        layout.addLayout(self.buttons)
        layout.addStretch()

    def show_state(self, step: StepState) -> None:
        mark = {DONE: "✓", CURRENT: "●", UPCOMING: "○"}[step.state]
        color = {DONE: coloring.GOOD.name(), CURRENT: "palette(highlight)", UPCOMING: "gray"}[step.state]
        self.title.setText(f"<span style='color:{color}'>{mark}</span>&nbsp; <b>{self._title_text}</b>")
        self.status.setText(step.status)
        self.status.setStyleSheet("color: gray; font-size: 11px;" if not step.enabled else "font-size: 11px;")
        border = "2px solid palette(highlight)" if step.state == CURRENT else "1px solid palette(mid)"
        self.setStyleSheet(f"QFrame#step {{ border: {border}; border-radius: 6px; }}")
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (label, callback) in enumerate(step.actions):
            button = QPushButton(label)
            button.setEnabled(step.enabled)
            button.setDefault(i == 0 and step.state == CURRENT)
            button.clicked.connect(callback)
            self.buttons.addWidget(button)
        self.buttons.addStretch()


class StepStrip(QWidget):
    TITLES = ["Inspect", "Transform", "Compare", "Submit"]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(8)
        self.steps = [_Step(i + 1, title) for i, title in enumerate(self.TITLES)]
        for step in self.steps:
            layout.addWidget(step, 1)

    def show_states(self, states: list[StepState]) -> None:
        for widget, state in zip(self.steps, states):
            widget.show_state(state)
