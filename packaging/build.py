"""Build the standalone app, self-test it, and zip it for a release.

From the project folder, with the build extra installed (pip install -e ".[build]"):

    python packaging/build.py

Writes dist/PhotometryWorkbench-<version>-macos.zip or -windows.zip.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Photometry Workbench"


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                    str(ROOT / "packaging/photometry-workbench.spec")], cwd=ROOT, check=True)

    dist = ROOT / "dist"
    if sys.platform == "darwin":
        app, platform = dist / f"{NAME}.app", "macos"
        exe = app / "Contents/MacOS" / NAME
    elif sys.platform == "win32":
        app, platform = dist / NAME, "windows"
        exe = app / f"{NAME}.exe"
    else:
        raise SystemExit("Builds are made on macOS or Windows.")

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "self-test.txt"
        result = subprocess.run([str(exe), "--self-test", str(report)])
        print(report.read_text() if report.exists() else "The self-test wrote no report.")
        if result.returncode != 0:
            raise SystemExit("Self-test failed; not zipping.")

    archive = dist / f"PhotometryWorkbench-{version}-{platform}.zip"
    archive.unlink(missing_ok=True)
    if platform == "macos":
        # ditto keeps the bundle's symlinks and signature intact, which a plain zip doesn't.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)], check=True)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", dist, NAME)
    print(f"Wrote {archive.relative_to(ROOT)} ({archive.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
