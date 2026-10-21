"""Share Plot dialog: a presentation-ready light curve for any or all filters (as
reported or transformed), with a choice of time axis, exportable as a JPEG for
posting elsewhere."""

from __future__ import annotations

from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from photometry_workbench.models.session import Session
from photometry_workbench.ui import plotting
from photometry_workbench.ui.chart_detail_dialog import FilterCheckboxRow

_TIME_MODES = [("Julian Date (JD)", "jd"), ("UTC", "utc"), ("Local time", "local")]


class SharePlotDialog(QDialog):
    """Pick any or all filters' target light curve (as reported, or transformed once the
    transform has been applied) and a time axis format, then export the result
    as a JPEG. Each filter is drawn in its own color, matching the rest of the app."""

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"{session.star_name} — Share Plot")
        self.resize(900, 640)

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.filter_row = FilterCheckboxRow(session.filters, self._refresh)
        controls.addWidget(self.filter_row)
        controls.addSpacing(16)

        self.transformed_check = QCheckBox("Transformed")
        self.transformed_check.setEnabled(session.has_after_report)
        self.transformed_check.stateChanged.connect(self._refresh)
        controls.addWidget(self.transformed_check)

        controls.addSpacing(16)
        controls.addWidget(QLabel("Time axis:"))
        self.time_combo = QComboBox()
        for label, _mode in _TIME_MODES:
            self.time_combo.addItem(label)
        self.time_combo.currentIndexChanged.connect(self._refresh)
        controls.addWidget(self.time_combo)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.figure = Figure(figsize=(8, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        export_button = QPushButton("Export as JPEG…")
        export_button.clicked.connect(self._export_jpg)
        button_row.addWidget(export_button)
        layout.addLayout(button_row)

        self._refresh()

    def _selected_filters(self) -> list[str]:
        return self.filter_row.selected()

    def _selected_time_mode(self) -> str:
        return _TIME_MODES[self.time_combo.currentIndex()][1]

    def _use_transformed(self) -> bool:
        return self.transformed_check.isChecked() and self.session.has_after_report

    def _refresh(self) -> None:
        filters = self._selected_filters()
        use_after = self._use_transformed()
        df = self.session.target_light_curve(filters, use_after=use_after)

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        plotting.plot_share_light_curve(ax, df, self._selected_time_mode(), filters)

        filt_label = "+".join(filters) if filters else "No Filter Selected"
        title = f"{self.session.star_name} — {filt_label} Light Curve"
        if use_after:
            title += " (Transformed)"
        subtitle = self.session.session_date.isoformat() if self.session.session_date else ""
        ax.set_title(f"{title}\n{subtitle}" if subtitle else title, fontsize=12)

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _export_jpg(self) -> None:
        filters = self._selected_filters()
        suffix = "_transformed" if self._use_transformed() else ""
        star_slug = self.session.star_name.replace(" ", "_")
        filt_slug = "".join(filters) if filters else "None"
        default_name = f"{star_slug}_{filt_slug}{suffix}.jpg"
        source = self.session.aavso.source_path
        folder = source.parent if source else Path.home()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Plot as JPEG", str(folder / default_name), "JPEG Image (*.jpg *.jpeg)"
        )
        if not path:
            return
        if not path.lower().endswith((".jpg", ".jpeg")):
            path += ".jpg"
        try:
            self.figure.savefig(path, format="jpg", dpi=200, facecolor="white")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
