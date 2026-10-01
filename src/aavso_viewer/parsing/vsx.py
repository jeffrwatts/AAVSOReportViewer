"""Fetch a variable star's known V-band amplitude from AAVSO's public VSX database.

Ported from aavso_report_check.ipynb's fetch_vsx_range. Used as a sanity check that a
session's observed magnitude range for the target roughly matches what VSX already
has on record -- not a precision check (VSX's range is just the observed min/max in
its own database, not an authoritative catalog value), so a small overshoot is
expected and tolerated (see coloring.RANGE_TOLERANCE).
"""

from __future__ import annotations

import requests

from aavso_viewer.parsing.aavso_api import auth_headers

_VSX_URL = "https://vsx.aavso.org/index.php"


def _parse_v_mag(s: str) -> float | None:
    parts = s.split()
    if len(parts) == 2 and parts[1] == "V":
        return float(parts[0])
    return None


def fetch_vsx_range(star_name: str, timeout: float = 8.0) -> tuple[float, float] | None:
    """Returns (bright, faint) V-band magnitudes -- confusingly, VSX's own field names
    are the reverse of magnitude-number order ('MaxMag' is the star at its brightest,
    i.e. the smaller number) -- or None if VSX has no V-band amplitude for this star.
    Raises on network/parse failure; callers should catch and degrade gracefully,
    since this is an optional online enhancement to an otherwise fully offline tool."""
    resp = requests.get(
        _VSX_URL,
        params={"view": "api.object", "ident": star_name, "format": "json"},
        headers=auth_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    obj = resp.json().get("VSXObject", {})

    bright = _parse_v_mag(obj.get("MaxMag", ""))
    faint = _parse_v_mag(obj.get("MinMag", ""))
    if bright is None or faint is None:
        return None
    return bright, faint
