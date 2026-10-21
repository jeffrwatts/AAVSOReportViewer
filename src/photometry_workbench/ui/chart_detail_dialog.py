from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from photometry_workbench.models.session import Session
from photometry_workbench.ui import plotting


class FilterCheckboxRow(QWidget):
    def __init__(self, filters: list[str], on_change, parent=None):
        super().__init__(parent)
        self._boxes: dict[str, QCheckBox] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Filters:"))
        for filt in filters:
            box = QCheckBox(filt)
            box.setChecked(True)
            box.stateChanged.connect(on_change)
            layout.addWidget(box)
            self._boxes[filt] = box
        layout.addStretch(1)

    def selected(self) -> list[str]:
        return [f for f, box in self._boxes.items() if box.isChecked()]


class _BaseDetailDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 480)
        self.layout_ = QVBoxLayout(self)

        self.controls_row = QHBoxLayout()
        self.layout_.addLayout(self.controls_row)

        self.figure = Figure(figsize=(7, 4.5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.layout_.addWidget(self.canvas)

    def _redraw(self, plot_fn) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        plot_fn(ax)
        self.figure.tight_layout()
        self.canvas.draw_idle()


class LightCurveDetailDialog(_BaseDetailDialog):
    """Target/Check Light Curve detail: filter multi-select plus a target/check/both toggle."""

    def __init__(self, session: Session, parent=None):
        super().__init__(f"{session.star_name} — Light Curve", parent)
        self.session = session

        self.filter_row = FilterCheckboxRow(session.filters, self._refresh)
        self.controls_row.addWidget(self.filter_row)

        self.role_target = QCheckBox("Target")
        self.role_target.setChecked(True)
        self.role_target.stateChanged.connect(self._refresh)
        self.role_check = QCheckBox("Check")
        self.role_check.setChecked(True)
        self.role_check.stateChanged.connect(self._refresh)
        self.controls_row.addWidget(self.role_target)
        self.controls_row.addWidget(self.role_check)
        self.controls_row.addStretch(1)

        self._refresh()

    def _refresh(self) -> None:
        filters = self.filter_row.selected()
        roles = tuple(
            r for r, box in (("target", self.role_target), ("check", self.role_check)) if box.isChecked()
        )

        def plot(ax):
            df = self.session.target_check_light_curve(filters=filters or None)
            if roles:
                plotting.plot_light_curve(ax, df, roles=roles, thumbnail=False)
            ax.set_title("Target / Check Light Curve")

        self._redraw(plot)


class CompStarsDetailDialog(_BaseDetailDialog):
    """Comp Stars Light Curve detail: filter multi-select plus a checkbox per comparison star."""

    def __init__(self, session: Session, parent=None):
        super().__init__(f"{session.star_name} — Comparison Star Curves", parent)
        self.session = session
        self.stars = session.comp_star_names()

        self.filter_row = FilterCheckboxRow(session.filters, self._refresh)
        self.controls_row.addWidget(self.filter_row)

        self.star_boxes: dict[str, QCheckBox] = {}
        for star in self.stars:
            box = QCheckBox(star)
            box.setChecked(True)
            box.stateChanged.connect(self._refresh)
            self.controls_row.addWidget(box)
            self.star_boxes[star] = box
        self.controls_row.addStretch(1)

        self._refresh()

    def _refresh(self) -> None:
        filters = self.filter_row.selected()
        stars = [s for s, box in self.star_boxes.items() if box.isChecked()]

        def plot(ax):
            df = self.session.comp_stars_light_curve(filters=filters or None, stars=stars)
            if not df.empty:
                plotting.plot_comp_stars(ax, df, thumbnail=False)
            ax.set_title("Comparison Star Curves")

        self._redraw(plot)


class DiagnosticDetailDialog(_BaseDetailDialog):
    """SNR / FWHM / Skyglow / Max ADU / Airmass detail: filter multi-select only --
    these are single-series per the General Report's schema, no target/check split."""

    def __init__(self, session: Session, column: str, title: str, ylabel: str, *, is_airmass: bool = False, parent=None):
        super().__init__(f"{session.star_name} — {title}", parent)
        self.session = session
        self.column = column
        self.title_text = title
        self.ylabel = ylabel
        self.is_airmass = is_airmass

        self.filter_row = FilterCheckboxRow(session.filters, self._refresh)
        self.controls_row.addWidget(self.filter_row)
        self.controls_row.addStretch(1)

        self._refresh()

    def _refresh(self) -> None:
        filters = self.filter_row.selected()

        def plot(ax):
            if self.is_airmass:
                df = self.session.airmass_series(filters=filters or None)
                if not df.empty:
                    plotting.plot_airmass(ax, df, thumbnail=False)
            else:
                df = self.session.diagnostic_series(self.column, filters=filters or None)
                if not df.empty:
                    plotting.plot_diagnostic(ax, df, thumbnail=False, ylabel=self.ylabel)
            ax.set_title(self.title_text)

        self._redraw(plot)


class CheckBeforeAfterDetailDialog(_BaseDetailDialog):
    """Check star's magnitude before vs. after the transform -- filter multi-select
    only (a catalog-reference line is drawn when a single filter is selected)."""

    def __init__(self, session: Session, parent=None):
        super().__init__(f"{session.star_name} — Check Star Before/After", parent)
        self.session = session

        self.filter_row = FilterCheckboxRow(session.filters, self._refresh)
        self.controls_row.addWidget(self.filter_row)
        self.controls_row.addStretch(1)

        self._refresh()

    def _refresh(self) -> None:
        filters = self.filter_row.selected()

        def plot(ax):
            plotting.plot_check_before_after(ax, self.session, thumbnail=False, filters=filters or None)
            ax.set_title("Check Star Before/After")

        self._redraw(plot)


class TargetBeforeAfterDetailDialog(_BaseDetailDialog):
    """Target's magnitude before vs. after the transform -- filter multi-select only."""

    def __init__(self, session: Session, parent=None):
        super().__init__(f"{session.star_name} — Target Before/After", parent)
        self.session = session

        self.filter_row = FilterCheckboxRow(session.filters, self._refresh)
        self.controls_row.addWidget(self.filter_row)
        self.controls_row.addStretch(1)

        self._refresh()

    def _refresh(self) -> None:
        filters = self.filter_row.selected()

        def plot(ax):
            plotting.plot_target_before_after(ax, self.session, thumbnail=False, filters=filters or None)
            ax.set_title("Target Before/After")

        self._redraw(plot)
