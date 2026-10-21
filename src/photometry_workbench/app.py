from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from photometry_workbench.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()  # opens empty; step 1 loads a report
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
