from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from photometry_workbench.ui.main_window import MainWindow


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from photometry_workbench.self_test import run

        return run(sys.argv[2])
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources/icon.png")))
    window = MainWindow()  # opens empty; step 1 loads a report
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
