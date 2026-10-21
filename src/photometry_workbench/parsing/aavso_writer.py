"""Writer for the AAVSO Extended-format photometry report -- the inverse of
parsing.aavso_report, used to produce the file that actually gets submitted."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from photometry_workbench.parsing.aavso_report import STANDARD_COLUMNS, AavsoReport

# Header parameters, in the order VPhot and TransformApplier write them.
HEADER_ORDER = ["TYPE", "OBSCODE", "SOFTWARE", "DELIM", "DATE", "OBSTYPE"]

# Field length limits from AAVSO's Extended format spec
# (https://docs.aavso.org/file-formats/aavso-aid-file-format.md). NOTES may run to
# several thousand characters, so it isn't checked here.
FIELD_LIMITS = {
    "NAME": 30, "DATE": 16, "MAG": 8, "MERR": 6, "CNAME": 20, "CMAG": 8,
    "KNAME": 20, "KMAG": 8, "AMASS": 7, "GROUP": 5, "CHART": 20,
}
SOFTWARE_LIMIT = 255

# Decimal places per numeric field: JD needs six per the spec; the rest match VPhot.
_DECIMALS = {"DATE": 6, "MAG": 3, "MERR": 3, "CMAG": 3, "KMAG": 3, "AMASS": 4}

ROW_COMMENTS_COLUMN = "_PRE"
"""Optional per-row column: a list of '#' comment lines written just before that row
(TA-style provenance -- the original untransformed row, the frames it was paired with)."""


def _format_field(column: str, value) -> str:
    if column in _DECIMALS:
        if value is None or pd.isna(value):
            return "na"
        return f"{float(value):.{_DECIMALS[column]}f}"
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "na"
    return str(value)


def format_row(row: pd.Series, delim: str = ",") -> str:
    fields = []
    for column in STANDARD_COLUMNS:
        text = _format_field(column, row.get(column))
        if delim in text:
            raise ValueError(f"{column} contains the delimiter {delim!r}: {text!r}")
        limit = FIELD_LIMITS.get(column)
        if limit is not None and len(text) > limit:
            raise ValueError(f"{column} is longer than {limit} characters: {text!r}")
        fields.append(text)
    return delim.join(fields)


def format_report(report: AavsoReport) -> str:
    header = dict(report.header)
    header.setdefault("TYPE", "EXTENDED")
    header.setdefault("DELIM", ",")
    header.setdefault("DATE", "JD")
    delim = header["DELIM"]
    if len(header.get("SOFTWARE", "")) > SOFTWARE_LIMIT:
        raise ValueError(f"SOFTWARE is longer than {SOFTWARE_LIMIT} characters")

    lines = [f"#{key}={header[key]}" for key in HEADER_ORDER if key in header]
    lines += [f"#{key}={value}" for key, value in header.items() if key not in HEADER_ORDER]
    lines.append("#" + delim.join(STANDARD_COLUMNS))
    lines += [c if c.startswith("#") else "#" + c for c in report.comments]
    has_row_comments = ROW_COMMENTS_COLUMN in report.df.columns
    for _, row in report.df.iterrows():
        if has_row_comments and isinstance(row[ROW_COMMENTS_COLUMN], list):
            lines += row[ROW_COMMENTS_COLUMN]
        lines.append(format_row(row, delim))
    return "\n".join(lines) + "\n"


def write_aavso_report(report: AavsoReport, path: str | Path) -> None:
    """Validates every row before touching the file, so a bad field never leaves a
    half-written report behind."""
    text = format_report(report)
    Path(path).write_text(text)
