from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from aavso_viewer.models.session import Session
from aavso_viewer.parsing.aavso_report import parse_aavso_report
from aavso_viewer.parsing.general_report import parse_general_report
from aavso_viewer.ui.main_window import MainWindow
from aavso_viewer.ui.startup_dialog import StartupDialog


def main() -> int:
    app = QApplication(sys.argv)

    session: Session | None = None
    transformed_path: str | None = None

    while session is None:
        dialog = StartupDialog()
        if dialog.exec() != QDialog.Accepted:
            return 0
        try:
            report = parse_aavso_report(dialog.aavso_report_path())
            general_path = dialog.general_report_path()
            general = parse_general_report(general_path) if general_path else None
            transformed_path = dialog.transformed_report_path()
            after = parse_aavso_report(transformed_path) if transformed_path else None
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(None, "Failed to load report", str(exc))
            continue
        session = Session(report, general)
        if after is not None:
            session.attach_after_report(after)

    window = MainWindow(session)
    window.transformed_report_path = transformed_path
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
