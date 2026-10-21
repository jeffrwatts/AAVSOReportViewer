from __future__ import annotations

from pathlib import Path

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

from photometry_workbench.parsing.general_report import EXPORT_HELP
from photometry_workbench.ui.app_settings import last_folder


class _FilePickerRow:
    def __init__(self, grid: QGridLayout, row: int, label: str, description: str, start_folder=last_folder):
        self._start_folder = start_folder
        """Callable giving the folder Browse… opens in when the field is empty."""
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
        current = self.path()
        start = str(Path(current).parent) if current else self._start_folder()
        path, _ = QFileDialog.getOpenFileName(
            None, "Select file", start, "Text files (*.txt);;All files (*)"
        )
        if path:
            self.edit.setText(path)

    def path(self) -> str:
        return self.edit.text().strip()


class StartupDialog(QDialog):
    """Step 1's Open Report dialog: AAVSO Report (required) and General Report
    (optional)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open Report")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        grid = QGridLayout()
        layout.addLayout(grid)

        self.aavso_row = _FilePickerRow(
            grid,
            0,
            "AAVSO Report",
            "The AAVSO Extended-format report to analyze, transformed or untransformed.",
        )
        self.general_row = _FilePickerRow(
            grid,
            2,
            "General Report",
            "VPhot's General Export for this session. Adds SNR, FWHM, Skyglow, Max "
            "ADU, and (for ensembles) each comparison star, which the uncertainty needs. "
            + EXPORT_HELP,
            start_folder=self._aavso_folder,
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Open")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._buttons = buttons

    def _aavso_folder(self) -> str:
        """The General Report is usually saved next to the AAVSO Report."""
        aavso = self.aavso_row.path()
        return str(Path(aavso).parent) if aavso else last_folder()

    def _on_accept(self) -> None:
        if not self.aavso_row.path():
            self.aavso_row.edit.setPlaceholderText("Required")
            self.aavso_row.edit.setStyleSheet("border: 1px solid red;")
            return
        self.accept()

    def aavso_report_path(self) -> str:
        return self.aavso_row.path()

    def general_report_path(self) -> str | None:
        return self.general_row.path() or None
