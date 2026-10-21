from __future__ import annotations

from typing import Callable

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class _ClickableCanvas(FigureCanvasQTAgg):
    clicked = Signal()

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        self.clicked.emit()
        super().mousePressEvent(event)


class ChartThumbnail(QFrame):
    """A small clickable chart. Click anywhere (matching VPhot's own 'click charts
    for detail' pattern) to open the full detail view."""

    clicked = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-weight: 600; font-size: 11px;")
        layout.addWidget(self._title_label)

        self.figure = Figure(figsize=(3.0, 2.0), dpi=100)
        self.canvas = _ClickableCanvas(self.figure)
        self.canvas.setFixedHeight(170)
        self.canvas.clicked.connect(self.clicked.emit)
        layout.addWidget(self.canvas)

    def mousePressEvent(self, event):  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_unavailable(self) -> None:
        """Render the '*' placeholder shown when the General Report needed for this
        chart hasn't been loaded yet. Still clickable -- opens the file picker."""
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.text(0.5, 0.5, "*", ha="center", va="center", fontsize=32, color="#999999")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def render(self, plot_fn: Callable) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        plot_fn(ax, thumbnail=True)
        self.figure.tight_layout(pad=0.3)
        self.canvas.draw_idle()
