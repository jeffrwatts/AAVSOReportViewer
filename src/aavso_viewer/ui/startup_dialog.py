from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class _FilePickerRow:
    def __init__(self, grid: QGridLayout, row: int, label: str, description: str):
        self.edit = QLineEdit()
        self.edit.setMinimumWidth(360)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)

        grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(self.edit, row, 1)
        grid.addWidget(browse, row, 2)
        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: gray; font-size: 11px;")
        grid.addWidget(desc_label, row + 1, 1, 1, 2)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            None, "Select file", "", "Text files (*.txt);;All files (*)"
        )
        if path:
            self.edit.setText(path)

    def path(self) -> str:
        return self.edit.text().strip()


class StartupDialog(QDialog):
    """Startup dialog: AAVSO Report (required), Transformed Report (optional --
    TransformApplier's output; leave it empty to transform in the app via the
    Transform menu), General Report (optional)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AAVSO Report Viewer")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        grid = QGridLayout()
        layout.addLayout(grid)

        self.aavso_row = _FilePickerRow(
            grid,
            0,
            "AAVSO Report",
            "The AAVSO Extended-format report to analyze — transformed or untransformed.",
        )
        self.transformed_row = _FilePickerRow(
            grid,
            2,
            "Transformed Report",
            "TransformApplier's _TA report for this session, for the before/after comparison "
            "and the submission report. Leave empty to transform in the app (Transform menu).",
        )
        self.general_row = _FilePickerRow(
            grid,
            4,
            "General Report",
            "VPhot's General Export for this session — adds SNR, FWHM, Skyglow, Max "
            "ADU, and (for ensembles) individual comparison star curves.",
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._buttons = buttons

    def _on_accept(self) -> None:
        if not self.aavso_row.path():
            self.aavso_row.edit.setPlaceholderText("Required")
            self.aavso_row.edit.setStyleSheet("border: 1px solid red;")
            return
        self.accept()

    def aavso_report_path(self) -> str:
        return self.aavso_row.path()

    def transformed_report_path(self) -> str | None:
        return self.transformed_row.path() or None

    def general_report_path(self) -> str | None:
        return self.general_row.path() or None
