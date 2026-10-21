"""Parser for VPhot's General Export (tab-delimited per-observation diagnostics)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

FIXED_COLUMNS = ("JD", "Filter", "Airmass")
# The diagnostic columns are optional/user-configurable in VPhot's export dialog --
# a real file may carry any subset of these, so detect by name, never by position.
DIAGNOSTIC_COLUMNS = ("Err", "SNR", "FWHM", "Skyglow", "Max ADU", "I.M.")


@dataclass
class GeneralReport:
    df: pd.DataFrame
    star_columns: list[str]
    """Column names carrying a standardized magnitude, one per star (target, check,
    and -- for an ensemble session -- each named comparison star). Confirmed by exact
    match against the AAVSO report's own MAG/KMAGSTD fields for the same exposure --
    only the separate 'I.M.' diagnostic column is genuinely raw instrumental (for
    whichever single star was selected at export time)."""
    diagnostic_columns: list[str]
    """Whichever of DIAGNOSTIC_COLUMNS are actually present in this file."""


def parse_general_report(path: str | Path) -> GeneralReport:
    path = Path(path)
    df = pd.read_csv(path, sep="\t")
    df.columns = [c.strip() for c in df.columns]

    diagnostic_columns = [c for c in DIAGNOSTIC_COLUMNS if c in df.columns]
    star_columns = [
        c for c in df.columns if c not in FIXED_COLUMNS and c not in diagnostic_columns
    ]

    for c in df.columns:
        if c != "Filter":
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return GeneralReport(df=df, star_columns=star_columns, diagnostic_columns=diagnostic_columns)
