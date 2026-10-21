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

Std here is the per-frame standard deviation, not the standard deviation of the mean --
for the comp spread too, as the uncertainty course's Technique #3 intends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from photometry_workbench import APP_NAME, app_version
from photometry_workbench.parsing.aavso_report import (
    STANDARD_COLUMNS,
    AavsoReport,
    _parse_notes,
    report_from_frame,
)
from photometry_workbench.parsing.aavso_writer import ROW_COMMENTS_COLUMN, format_row
from photometry_workbench.transform.engine import ensemble_cross_check

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


def _per_frame(
    report: AavsoReport, method: ErrorMethod, comp_spread: pd.DataFrame | None
) -> tuple[pd.DataFrame, list[str]]:
    out = report.df[[c for c in [*STANDARD_COLUMNS, ROW_COMMENTS_COLUMN] if c in report.df.columns]].copy()
    extra = {j: {"MERRMETHOD": method.value} for j in out.index}
    warnings: list[str] = []
    if method == ErrorMethod.COMP_SPREAD:
        if comp_spread is None:
            raise SubmissionError("The comparison-star spread hasn't been computed for this session.")
        spread = comp_spread.reindex(out.index)
        short = int((spread["n"].fillna(0) < 2).sum())
        if short:
            raise SubmissionError(
                f"{short} frame(s) have fewer than 2 comparison stars measured -- no spread to report."
            )
        out["MERR"] = np.hypot(spread["std"], out["MERR"]).round(3)
        for j in out.index:
            extra[j].update(NCOMP=str(int(spread.at[j, "n"])), COMPSTD=f"{spread.at[j, 'std']:.3f}")
        problem = ensemble_cross_check(report.df["MAG"], spread, int(spread["n"].max()))
        if problem:
            warnings.append(problem)
    out["NOTES"] = [
        "|" + "|".join(f"{k}={v}" for k, v in {**_parse_notes(n), **extra[j]}.items())
        for j, n in out["NOTES"].items()
    ]
    return out, warnings


@dataclass(frozen=True)
class FrameConsistency:
    """One filter of the consistency check for a combined point: the
    uncertainty a single frame carries vs the scatter actually seen across the frames.
    The scatter should be about the same or lower; well above it (CONSISTENCY_LIMIT),
    something is off -- one bad frame, clouds -- worth tracking down."""

    filt: str
    single: float
    """Median per-frame uncertainty: MERR, or for an ensemble with its comp spread
    known, that spread combined with MERR (Technique #3)."""
    across: float
    """Std of the target's magnitude across the frames -- the combined point's MERR."""

    @property
    def ratio(self) -> float:
        return self.across / self.single if self.single else np.nan


# AAVSO mentor guidance: a Std across frames 1.5x or more the single-frame estimate
# means something needs tracking down.
CONSISTENCY_LIMIT = 1.5


def frame_consistency(report: AavsoReport, comp_spread: pd.DataFrame | None = None) -> list[FrameConsistency]:
    """Per filter, the single-frame uncertainty vs the Std across frames, for a report
    whose frames will be combined (ErrorMethod.STD_TARGET). `comp_spread`
    (Session.comp_spread, indexed like report.df) is used for an ensemble's
    single-frame uncertainty when given."""
    df = report.df
    single = df["MERR"]
    if comp_spread is not None:
        spread = comp_spread.reindex(df.index)["std"]
        single = pd.Series(np.where(spread.notna(), np.hypot(spread, df["MERR"]), df["MERR"]), index=df.index)
    result = []
    for filt in report.filters:
        rows = df["FILT"] == filt
        if rows.sum() < 2:
            continue
        result.append(FrameConsistency(filt, float(single[rows].median()), float(df.loc[rows, "MAG"].std(ddof=1))))
    return result


def build_submission(
    report: AavsoReport,
    method: ErrorMethod,
    comp_spread: pd.DataFrame | None = None,
) -> Submission:
    """`comp_spread` (Session.comp_spread, indexed like report.df) is required for
    ErrorMethod.COMP_SPREAD and ignored otherwise."""
    if method in (ErrorMethod.STD_TARGET, ErrorMethod.STD_CHECK):
        out, warnings = _combine(report, method)
    else:
        out, warnings = _per_frame(report, method, comp_spread)

    header = dict(report.header)
    original = header.get("SOFTWARE")
    ours = f"{APP_NAME} {app_version()}"
    header["SOFTWARE"] = f"{original} + {ours}" if original else ours
    comments = [*report.comments, f"#  {ours}: {method.description}"]
    return Submission(
        report_from_frame(out, header=header, comments=comments, source_path=report.source_path),
        method,
        warnings,
    )
