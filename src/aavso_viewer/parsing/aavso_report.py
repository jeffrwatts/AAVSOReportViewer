"""Parser for the AAVSO Extended-format photometry report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


def _parse_notes(notes_str: str) -> dict:
    """AAVSO NOTES field is '|KEY=VAL|KEY=VAL|...'."""
    parsed: dict[str, str] = {}
    if not isinstance(notes_str, str):
        return parsed
    for part in notes_str.strip("|").split("|"):
        if "=" in part:
            key, val = part.split("=", 1)
            parsed[key] = val
    return parsed


@dataclass
class AavsoReport:
    df: pd.DataFrame
    star_name: str
    is_transformed: bool
    is_ensemble: bool
    filters: list[str]
    """Filters in first-seen order, as observations were reported."""


def parse_aavso_report(path: str | Path) -> AavsoReport:
    """Parse an AAVSO Extended-format report (single-comparison or ensemble,
    transformed or untransformed) into a flat DataFrame plus session metadata.

    A TransformApplier-produced report keeps the original untransformed rows as
    '#'-commented lines alongside the live TRANS=YES replacement rows -- those
    commented rows are skipped like any other comment line.
    """
    path = Path(path)
    delim = ","
    header: list[str] | None = None
    data_lines: list[list[str]] = []

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        if line.startswith("#DELIM="):
            delim = line.split("=", 1)[1].strip()
        elif line.startswith("#NAME" + delim):
            header = line.lstrip("#").split(delim)
        elif line.startswith("#"):
            continue
        else:
            data_lines.append(line.split(delim))

    if header is None:
        raise ValueError(f"Could not find column header line in {path}")

    df = pd.DataFrame(data_lines, columns=header)

    for col in ["DATE", "MAG", "MERR", "CMAG", "KMAG", "AMASS"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    notes = df["NOTES"].apply(_parse_notes) if "NOTES" in df.columns else pd.Series([{}] * len(df))
    df["KREFMAG"] = notes.apply(lambda d: float(d["KREFMAG"]) if "KREFMAG" in d else np.nan)
    df["KREFERR"] = notes.apply(lambda d: float(d["KREFERR"]) if "KREFERR" in d else np.nan)
    df["CREFMAG"] = notes.apply(lambda d: float(d["CREFMAG"]) if "CREFMAG" in d else np.nan)
    # KMAG is standardized in ENSEMBLE reports but raw instrumental in single-comp/check
    # reports (matches KMAGINS there); KMAGSTD in NOTES is the standardized value in both.
    df["KMAGSTD"] = notes.apply(lambda d: float(d["KMAGSTD"]) if "KMAGSTD" in d else np.nan)
    # KMAGTRAN only appears on TRANS=YES rows (transform-corrected check star mag).
    df["KMAGTRAN"] = notes.apply(lambda d: float(d["KMAGTRAN"]) if "KMAGTRAN" in d else np.nan)
    df["KEFF"] = df["KMAGTRAN"].fillna(df["KMAGSTD"])

    star_names = df["NAME"].unique()
    star_name = star_names[0] if len(star_names) else "?"

    is_transformed = bool((df.get("TRANS") == "YES").any())
    is_ensemble = bool(df["CNAME"].astype(str).str.upper().eq("ENSEMBLE").any())

    filters: list[str] = []
    for f in df["FILT"]:
        if f not in filters:
            filters.append(f)

    return AavsoReport(
        df=df,
        star_name=star_name,
        is_transformed=is_transformed,
        is_ensemble=is_ensemble,
        filters=filters,
    )
