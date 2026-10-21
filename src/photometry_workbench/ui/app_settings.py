"""Settings kept between runs (QSettings under the app's id)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from photometry_workbench import APP_ID

_LAST_FOLDER_KEY = "files/last_folder"


def settings() -> QSettings:
    return QSettings(APP_ID, APP_ID)


def last_folder() -> str:
    """The folder of the last report opened, for file dialogs to start in ("" if none)."""
    folder = settings().value(_LAST_FOLDER_KEY, "", type=str)
    return folder if folder and Path(folder).is_dir() else ""


def remember_folder(path: str | Path) -> None:
    settings().setValue(_LAST_FOLDER_KEY, str(Path(path).parent))
