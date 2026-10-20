"""Fetch comparison-star catalog magnitudes from AAVSO's public VSP chart API.

Used only for ensemble sessions: the AAVSO report's NOTES field carries a single
aggregate CREFMAG for the whole ensemble but never breaks out each named comparison
star's own catalog magnitude -- that per-star detail exists only in AAVSO's chart
database, keyed by the report's own CHART id. Single-comparison-star sessions already
get their one comp star's CREFMAG directly from the report's NOTES field and never
need this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

from aavso_viewer.parsing.aavso_api import APPS_BASE_URL, auth_headers

# www.aavso.org/vsp is Cloudflare-blocked for scripted requests -- see aavso_api.
_CHART_URL = APPS_BASE_URL + "/vsp/api/chart/{chart_id}/"

# The VSP API names the red/infrared bands "Rc"/"Ic" (Cousins system) rather than the
# AAVSO Extended report's plain "R"/"I" filter codes.
_BAND_TO_FILTER = {"Rc": "R", "Ic": "I"}

# Every successful chart fetch is saved here, and used when the chart service can't be
# reached (it was down for days in October 2026), so a session that loaded once keeps
# working offline.
CACHE_DIR = Path.home() / "Library/Caches/AAVSOReportViewer/charts"


@dataclass
class ChartMag:
    mag: float
    error: float | None
    """Catalog uncertainty -- the same role KREFERR plays for a check star: the
    yardstick for whether an observed delta from this catalog value is real."""


def _fetch_chart_json(chart_id: str, timeout: float) -> dict:
    """The chart's JSON from the VSP API, falling back to the last saved copy (see
    CACHE_DIR) if the request fails. Raises the request's error if there's no copy."""
    cached = CACHE_DIR / f"{chart_id}.json"
    try:
        resp = requests.get(
            _CHART_URL.format(chart_id=chart_id),
            params={"format": "json"},
            headers=auth_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        if cached.exists():
            return json.loads(cached.read_text())
        raise
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data))
    except OSError:
        pass  # the cache is a convenience; a read-only home shouldn't break the fetch
    return data


def _vphot_labels(stars: list[dict]) -> list[str]:
    """The chart's labels as VPhot names them. A chart can repeat a label for two
    different stars (e.g. two '105's); VPhot keeps the first and suffixes later ones
    in chart order -- '105', '105_1', '105_2' -- and its reports use those names.
    (Confirmed on chart X43295PS: VPhot's '105_1' and '119_1' are the second '105'
    and '119', by their V magnitudes and the report's averaged CREFMAG.)"""
    seen: dict[str, int] = {}
    labels = []
    for star in stars:
        label = str(star.get("label") or "").strip()
        if label:
            count = seen.get(label, 0)
            seen[label] = count + 1
            label = f"{label}_{count}" if count else label
        labels.append(label)
    return labels


def fetch_chart_auids(chart_id: str, timeout: float = 8.0) -> dict[str, str]:
    """Returns {star_label: AUID}, e.g. {"103": "000-BKZ-381"} -- AAVSO prefers AUIDs
    over chart labels in CNAME/KNAME, and TransformApplier substitutes them. Raises on
    any network/parse failure, like fetch_chart_photometry."""
    stars = _fetch_chart_json(chart_id, timeout).get("photometry", [])
    return {
        label: str(star["auid"]).strip()
        for label, star in zip(_vphot_labels(stars), stars)
        if label and star.get("auid")
    }


def fetch_chart_photometry(chart_id: str, timeout: float = 8.0) -> dict[str, dict[str, ChartMag]]:
    """Returns {star_label: {filter_code: ChartMag}}, e.g.
    {"103": {"V": ChartMag(10.334, 0.047)}, ...}. Raises on any network/parse failure
    -- callers should catch and degrade gracefully, since this is an optional online
    enhancement to an otherwise fully offline tool."""
    stars = _fetch_chart_json(chart_id, timeout).get("photometry", [])

    result: dict[str, dict[str, ChartMag]] = {}
    for label, star in zip(_vphot_labels(stars), stars):
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
