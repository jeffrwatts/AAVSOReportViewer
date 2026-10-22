"""A quick check that a built app works: `<app> --self-test results.txt`.

Used by the release builds, where there's no terminal output to read (the Windows app
has no console): it opens the main window offscreen, draws a chart, checks that the
version and icon came along, and tries one online lookup, then writes what it found
to the given file and exits 0 if everything needed passed.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def run(out_path: str) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: list[str] = []
    failed = False

    def check(name: str, fn, required: bool = True) -> None:
        nonlocal failed
        try:
            detail = fn()
            lines.append(f"ok    {name}: {detail}")
        except Exception:
            failed = failed or required
            lines.append(f"{'FAIL' if required else 'warn'}  {name}:")
            lines.extend("      " + line for line in traceback.format_exc().splitlines())

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    def version():
        from photometry_workbench import app_version

        v = app_version()
        if v == "dev":
            raise RuntimeError("package metadata missing (version shows 'dev')")
        return v

    def icon():
        path = Path(__file__).parent / "resources/icon.png"
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.name

    def window():
        from photometry_workbench.ui.main_window import MainWindow

        w = MainWindow()
        w.show()
        app.processEvents()
        title = w.windowTitle()
        w.close()
        return title

    def chart():
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        canvas = FigureCanvasQTAgg(Figure(figsize=(3, 2)))
        canvas.figure.add_subplot().plot([0, 1], [1, 0], "o")
        canvas.draw()
        return "drawn"

    def network():
        from photometry_workbench.parsing.vsx import fetch_vsx_range

        rng = fetch_vsx_range("CY Aqr")
        if rng is None:
            raise RuntimeError("no VSX range returned")
        return f"VSX CY Aqr {rng}"

    check("version", version)
    check("icon", icon)
    check("main window", window)
    check("chart", chart)
    check("online lookup", network, required=False)

    lines.append("PASSED" if not failed else "FAILED")
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0
