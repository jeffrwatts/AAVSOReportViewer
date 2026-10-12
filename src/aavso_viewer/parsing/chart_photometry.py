"""Fetch comparison-star catalog magnitudes from AAVSO's public VSP chart API.

Used only for ensemble sessions: the AAVSO report's NOTES field carries a single
aggregate CREFMAG for the whole ensemble but never breaks out each named comparison
star's own catalog magnitude -- that per-star detail exists only in AAVSO's chart
database, keyed by the report's own CHART id. Single-comparison-star sessions already
get their one comp star's CREFMAG directly from the report's NOTES field and never
need this.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from aavso_viewer.parsing.aavso_api import APPS_BASE_URL, auth_headers

# www.aavso.org/vsp is Cloudflare-blocked for scripted requests -- see aavso_api.
_CHART_URL = APPS_BASE_URL + "/vsp/api/chart/{chart_id}/"

# The VSP API names the red/infrared bands "Rc"/"Ic" (Cousins system) rather than the
# AAVSO Extended report's plain "R"/"I" filter codes.
_BAND_TO_FILTER = {"Rc": "R", "Ic": "I"}


@dataclass
class ChartMag:
    mag: float
    error: float | None
    """Catalog uncertainty -- the same role KREFERR plays for a check star: the
    yardstick for whether an observed delta from this catalog value is real."""


def _fetch_chart_json(chart_id: str, timeout: float) -> dict:
    resp = requests.get(
        _CHART_URL.format(chart_id=chart_id),
        params={"format": "json"},
        headers=auth_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_chart_auids(chart_id: str, timeout: float = 8.0) -> dict[str, str]:
    """Returns {star_label: AUID}, e.g. {"103": "000-BKZ-381"} -- AAVSO prefers AUIDs
    over chart labels in CNAME/KNAME, and TransformApplier substitutes them. Raises on
    any network/parse failure, like fetch_chart_photometry."""
    data = _fetch_chart_json(chart_id, timeout)
    return {
        str(star["label"]).strip(): str(star["auid"]).strip()
        for star in data.get("photometry", [])
        if star.get("label") and star.get("auid")
    }


def fetch_chart_photometry(chart_id: str, timeout: float = 8.0) -> dict[str, dict[str, ChartMag]]:
    """Returns {star_label: {filter_code: ChartMag}}, e.g.
    {"103": {"V": ChartMag(10.334, 0.047)}, ...}. Raises on any network/parse failure
    -- callers should catch and degrade gracefully, since this is an optional online
    enhancement to an otherwise fully offline tool."""
    data = _fetch_chart_json(chart_id, timeout)

    result: dict[str, dict[str, ChartMag]] = {}
    for star in data.get("photometry", []):
        label = str(star.get("label", "")).strip()
        if not label:
            continue
        mags: dict[str, ChartMag] = {}
        for band in star.get("bands", []):
            name, mag, error = band.get("band"), band.get("mag"), band.get("error")
            if name is None or mag is None:
                continue
            mags[_BAND_TO_FILTER.get(name, name)] = ChartMag(
                float(mag), float(error) if error is not None else None
            )
        result[label] = mags
    return result
