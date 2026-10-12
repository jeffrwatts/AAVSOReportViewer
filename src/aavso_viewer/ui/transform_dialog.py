"""Pick a TG coefficients file and preview how it will be applied."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from aavso_viewer.models.session import Session
from aavso_viewer.transform.coefficients import TransformCoefficients, parse_tg_ini
from aavso_viewer.transform.engine import describe_set, transform_set

SETTINGS_KEY = "transform/coefficients_path"


def settings() -> QSettings:
    return QSettings("AAVSOReportViewer", "AAVSOReportViewer")


class TransformDialog(QDialog):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Apply Transform")
        self.setMinimumWidth(560)
        self._coeffs: TransformCoefficients | None = None
        set_name, self._rules = transform_set(session.filters)
        self._used = {name for rule in self._rules.values() for name in (rule.coef, rule.color_coef)}

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("TG coefficients"))
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.description)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Coefficient", "Value", "Error"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        comps = "an ensemble (transformed as its average comp)" if session.is_ensemble else "a single comparison star"
        plan = QLabel(
            f"{session.star_name}: filters {', '.join(session.filters)}, {comps} "
            f"→ {set_name} set (coefficients in bold)\n" + "\n".join(describe_set(self._rules))
        )
        layout.addWidget(plan)
        if session.is_ensemble and not session.has_general_report:
            note = QLabel(
                "Without the General Report the app can't confirm every comparison star was measured "
                "in every filter, or list the ensemble's members in NOTES."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: gray; font-size: 11px;")
            layout.addWidget(note)

        self.auid_check = QCheckBox("Name comp/check stars by AUID (looks up the chart online, like TA)")
        self.auid_check.setChecked(True)
        layout.addWidget(self.auid_check)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #d32f2f;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._apply_button = buttons.button(QDialogButtonBox.Ok)

        last = settings().value(SETTINGS_KEY, "", type=str)
        if last and Path(last).exists():
            self._load(last)
        else:
            self._apply_button.setEnabled(False)

    def coefficients(self) -> TransformCoefficients | None:
        return self._coeffs

    def lookup_auids(self) -> bool:
        return self.auid_check.isChecked()

    def _browse(self) -> None:
        start = self.path_edit.text() or settings().value(SETTINGS_KEY, "", type=str)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select TG coefficients", str(Path(start).parent) if start else "",
            "TG coefficients (*.ini);;All files (*)",
        )
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        self.path_edit.setText(path)
        try:
            coeffs = parse_tg_ini(path)
            missing = sorted(self._used - coeffs.values.keys())
            if missing:
                raise ValueError(f"Missing coefficient(s) {', '.join(missing)}")
        except Exception as exc:  # noqa: BLE001
            self._coeffs = None
            self.error_label.setText(f"Can't use {Path(path).name}: {exc}")
            self.table.setRowCount(0)
            self.description.clear()
            self._apply_button.setEnabled(False)
            return
        self._coeffs = coeffs
        self.error_label.clear()
        self.description.setText(coeffs.description)
        self.table.setRowCount(len(coeffs.values))
        for r, (name, value) in enumerate(coeffs.values.items()):
            err = coeffs.error(name)
            cells = [name, f"{value:.4f}", "-" if err is None else f"{err:.4f}"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if name in self._used:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(r, c, item)
        self._apply_button.setEnabled(True)

    def _on_accept(self) -> None:
        if self._coeffs is None:
            return
        settings().setValue(SETTINGS_KEY, self.path_edit.text())
        self.accept()
