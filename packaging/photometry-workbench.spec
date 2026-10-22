# PyInstaller build of the standalone app: Photometry Workbench.app on macOS, a
# "Photometry Workbench" folder with Photometry Workbench.exe on Windows. From the
# project folder, with the build extra installed (pip install -e ".[build]"):
#
#     pyinstaller packaging/photometry-workbench.spec
#
# The result is in dist/. packaging/build.py does this and zips it for a release.

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
NAME = "Photometry Workbench"

# Qt modules the app never uses; leaving them out keeps the download much smaller.
UNUSED = [
    "tkinter", "IPython", "jupyter", "notebook", "pytest", "PyInstaller",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtHttpServer",
]

a = Analysis(
    [str(ROOT / "packaging/launch.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(ROOT / "src/photometry_workbench/resources"), "photometry_workbench/resources"),
        # The version shown in About and written to the report's SOFTWARE line.
        *copy_metadata("photometry-workbench"),
    ],
    excludes=UNUSED,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(ROOT / ("packaging/icon.icns" if sys.platform == "darwin" else "packaging/icon.ico")),
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(ROOT / "packaging/icon.icns"),
        bundle_identifier="com.jeffwatts.photometryworkbench",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
