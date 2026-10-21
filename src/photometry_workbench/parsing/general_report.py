"""Parser for VPhot's General Export (tab-delimited per-observation diagnostics)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

FIXED_COLUMNS = ("JD", "Filter", "Airmass")
# The diagnostic columns are optional/user-configurable in VPhot's export dialog --
# a real file may carry any subset of these, so detect by name, never by position.
DIAGNOSTIC_COLUMNS = ("Err", "SNR", "FWHM", "Skyglow", "Max ADU", "I.M.")

EXPORT_HELP = (
    "In VPhot's General Export, check every star and every data box, leave Truncated JD "
    "unchecked, keep the Tab separator, and use Save to Text."
)


class GeneralReportError(ValueError):
    """A General Report the app can't use as exported; the message says how to re-export."""


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

    @property
    def missing_diagnostics(self) -> list[str]:
        return [c for c in DIAGNOSTIC_COLUMNS if c not in self.diagnostic_columns]


def parse_general_report(path: str | Path) -> GeneralReport:
    path = Path(path)
    df = pd.read_csv(path, sep="\t")
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in ("JD", "Filter") if c not in df.columns]
    if missing:
        comma = len(df.columns) == 1 and "," in df.columns[0]
        why = "It looks comma-separated." if comma else f"It has no {' or '.join(missing)} column."
        raise GeneralReportError(f"This isn't a General Report the app can read. {why} {EXPORT_HELP}")

    diagnostic_columns = [c for c in DIAGNOSTIC_COLUMNS if c in df.columns]
    star_columns = [
        c for c in df.columns if c not in FIXED_COLUMNS and c not in diagnostic_columns
    ]

    for c in df.columns:
        if c != "Filter":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if df["JD"].notna().any() and df["JD"].max() < 2_400_000:
        raise GeneralReportError(
            "Its Julian Dates are truncated, so its frames can't be matched to the "
            f"AAVSO Report. {EXPORT_HELP}"
        )

    return GeneralReport(df=df, star_columns=star_columns, diagnostic_columns=diagnostic_columns)
