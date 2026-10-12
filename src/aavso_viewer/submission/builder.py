"""Build the report that actually gets submitted to AAVSO, choosing the reported
uncertainty (MERR) the way AAVSO's photometric-uncertainty course recommends.

The course's decision tree, as applied here (the target is always assumed bright,
so the CCD-equation branch never comes up):

- Several frames combined into one reported point (preferred):
  - target not expected to vary during the observation: Std of the target's frames.
  - target expected to vary: Std of a check star within ~2 mag of the target.
- Each frame reported on its own (a light curve):
  - several comparison stars (ensemble): spread of the target estimates across them.
  - one comparison star: the SNR-based per-frame MERR.

Std here is the per-frame standard deviation, not the standard deviation of the mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import PackageNotFoundError, version

import numpy as np
import pandas as pd

from aavso_viewer.parsing.aavso_report import (
    STANDARD_COLUMNS,
    AavsoReport,
    _parse_notes,
    report_from_frame,
)
from aavso_viewer.parsing.aavso_writer import ROW_COMMENTS_COLUMN, format_row

# The check star should be within this many magnitudes of the target for its Std to
# stand in for the target's uncertainty.
CHECK_MAG_LIMIT = 2.0


class ErrorMethod(str, Enum):
    STD_TARGET = "STD_TARGET"
    STD_CHECK = "STD_CHECK"
    SNR = "SNR"
    COMP_SPREAD = "COMP_SPREAD"

    @property
    def description(self) -> str:
        return {
            ErrorMethod.STD_TARGET: "Frames combined per filter; MERR = standard deviation of the target's frames.",
            ErrorMethod.STD_CHECK: "Frames combined per filter; MERR = standard deviation of the check star's frames "
            "(the target may vary during the observation).",
            ErrorMethod.SNR: "Each frame reported separately with one comparison star; MERR = the SNR-based "
            "per-frame uncertainty (TA's propagated value when transformed).",
            ErrorMethod.COMP_SPREAD: "Each frame reported separately with an ensemble; MERR = spread of the "
            "target estimates across the comparison stars, combined with the SNR uncertainty.",
        }[self]


def choose_method(combine: bool, target_varies: bool | None, is_ensemble: bool) -> ErrorMethod:
    if combine:
        if target_varies is None:
            raise ValueError("Combining frames needs an answer to whether the target varies.")
        return ErrorMethod.STD_CHECK if target_varies else ErrorMethod.STD_TARGET
    return ErrorMethod.COMP_SPREAD if is_ensemble else ErrorMethod.SNR


class SubmissionError(ValueError):
    pass


@dataclass
class Submission:
    report: AavsoReport
    method: ErrorMethod
    warnings: list[str] = field(default_factory=list)


def app_version() -> str:
    try:
        return version("aavso-viewer")
    except PackageNotFoundError:
        return "dev"


def _decimals(text: str) -> int:
    return len(text.split(".", 1)[1]) if "." in text else 0


def _merge_notes(notes: list[dict[str, str]]) -> dict[str, str]:
    """Keys whose value is the same on every frame are kept as-is; numeric keys that
    vary (instrumental mags, KMAGTRAN, ...) become their mean at the same precision;
    anything else that varies is dropped."""
    merged: dict[str, str] = {}
    for key in dict.fromkeys(k for n in notes for k in n):
        values = [n.get(key) for n in notes]
        if None in values:
            continue
        if len(set(values)) == 1:
            merged[key] = values[0]
            continue
        try:
            numbers = [float(v) for v in values]
        except ValueError:
            continue
        merged[key] = f"{np.mean(numbers):.{max(_decimals(v) for v in values)}f}"
    return merged


def _unique(rows: pd.DataFrame, column: str, filt: str) -> str:
    values = rows[column].astype(str).unique()
    if len(values) != 1:
        raise SubmissionError(f"{filt} frames don't share one {column} ({', '.join(values)}) -- can't combine them.")
    return values[0]


def _mean_or_nan(series: pd.Series) -> float:
    return float(series.mean()) if series.notna().any() else np.nan


def _combine(report: AavsoReport, method: ErrorMethod) -> tuple[pd.DataFrame, list[str]]:
    df = report.df
    delim = report.header.get("DELIM", ",")
    rows_out, warnings = [], []
    for filt in report.filters:
        rows = df.loc[df["FILT"] == filt]
        n = len(rows)
        if n < 2:
            raise SubmissionError(f"Only {n} {filt} frame -- a standard deviation needs at least 2.")
        trans = _unique(rows, "TRANS", filt)
        mag = float(rows["MAG"].mean())
        if method == ErrorMethod.STD_TARGET:
            merr = float(rows["MAG"].std(ddof=1))
        else:
            if rows["KEFF"].isna().any():
                raise SubmissionError(f"{filt} frames are missing a standardized check-star magnitude.")
            merr = float(rows["KEFF"].std(ddof=1))
            separation = abs(mag - rows["KEFF"].mean())
            if separation > CHECK_MAG_LIMIT:
                warnings.append(
                    f"{filt}: the check star is {separation:.2f} mag from the target -- more than "
                    f"{CHECK_MAG_LIMIT:g} mag, so its scatter may not represent the target's."
                )

        notes = _merge_notes([_parse_notes(n) for n in rows["NOTES"]])
        notes["NCOMB"] = str(n)
        notes["MERRMETHOD"] = method.value
        group = "0001" if trans == "YES" else (_unique(rows, "GROUP", filt) if rows["GROUP"].nunique() == 1 else "na")
        rows_out.append(
            {
                "NAME": _unique(rows, "NAME", filt),
                "DATE": round(float(rows["DATE"].mean()), 6),
                "MAG": round(mag, 3),
                "MERR": round(merr, 3),
                "FILT": filt,
                "TRANS": trans,
                "MTYPE": _unique(rows, "MTYPE", filt),
                "CNAME": _unique(rows, "CNAME", filt),
                "CMAG": _mean_or_nan(rows["CMAG"]),
                "KNAME": _unique(rows, "KNAME", filt),
                "KMAG": _mean_or_nan(rows["KMAG"]),
                "AMASS": _mean_or_nan(rows["AMASS"]),
                "GROUP": group,
                "CHART": _unique(rows, "CHART", filt),
                "NOTES": "|" + "|".join(f"{k}={notes[k]}" for k in sorted(notes)),
                ROW_COMMENTS_COLUMN: [f"# {filt}: mean of {n} frames, JD {rows['DATE'].min():.6f}-{rows['DATE'].max():.6f}"]
                + ["#" + format_row(r, delim) for _, r in rows.iterrows()],
            }
        )
    return pd.DataFrame(rows_out), warnings


def build_submission(report: AavsoReport, method: ErrorMethod) -> Submission:
    if method == ErrorMethod.COMP_SPREAD:
        raise SubmissionError(
            "Ensemble light curves (MERR from the comparison-star spread) aren't supported yet."
        )
    if method in (ErrorMethod.STD_TARGET, ErrorMethod.STD_CHECK):
        out, warnings = _combine(report, method)
    else:
        warnings = []
        out = report.df[[c for c in [*STANDARD_COLUMNS, ROW_COMMENTS_COLUMN] if c in report.df.columns]].copy()
        out["NOTES"] = [
            "|" + "|".join(f"{k}={v}" for k, v in {**_parse_notes(n), "MERRMETHOD": method.value}.items())
            for n in out["NOTES"]
        ]

    header = dict(report.header)
    original = header.get("SOFTWARE")
    ours = f"AAVSO Report Viewer {app_version()}"
    header["SOFTWARE"] = f"{original} + {ours}" if original else ours
    comments = [*report.comments, f"#  {ours}: {method.description}"]
    return Submission(
        report_from_frame(out, header=header, comments=comments, source_path=report.source_path),
        method,
        warnings,
    )
