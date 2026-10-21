"""Photometry Workbench: inspect, transform and prepare VPhot photometry reports for
submission to the AAVSO."""

from importlib.metadata import PackageNotFoundError, version

# The app's name lives here and in pyproject.toml only; everything else (window titles,
# the SOFTWARE header in submitted reports, settings and cache locations) uses these.
APP_NAME = "Photometry Workbench"
APP_ID = "PhotometryWorkbench"
DIST_NAME = "photometry-workbench"
PROJECT_URL = "https://github.com/jeffrwatts/PhotometryWorkbench"


def app_version() -> str:
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "dev"
