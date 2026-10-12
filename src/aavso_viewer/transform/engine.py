"""Apply TG transform coefficients to an untransformed AAVSO report, reproducing
AAVSO TransformApplier (TA)'s output for single-comparison-star sessions.

The math works per comparison star: each frame's target and check magnitudes are
transformed differentially against one comp star at a time, then averaged over comps.
A single-comp session is just the one-comp case; an ensemble would supply one
estimate per member (not supported yet -- see transform_report).

Verified against TA 2.71 output for three real sessions (SN 2026aaiv, CY Aqr,
YZ Boo; tests/test_transform.py): MAG, MERR and KMAGTRAN are identical on ~98% of
frames and never more than 0.001 apart. MERR uses TA's formula, which propagates
only the per-frame MERRs and leaves out the coefficient errors -- matched
deliberately, for parity.

One deliberate difference: frames are paired across filters by nearest time. TA
pairs the k-th frame of each filter, so after a missing frame it pairs every later
frame with the previous cycle's partner for the rest of the night.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from aavso_viewer.parsing.aavso_report import (
    STANDARD_COLUMNS,
    AavsoReport,
    _parse_notes,
    report_from_frame,
)
from aavso_viewer.parsing.aavso_writer import ROW_COMMENTS_COLUMN, format_row
from aavso_viewer.transform.coefficients import TransformCoefficients

# Farthest apart (in days, ~14 min) two frames in different filters can be and still
# be paired into one transform group. Normal cycles are a minute or two apart; this
# only stops a frame from being paired across a long gap.
_PAIR_TOLERANCE_DAYS = 0.01


@dataclass(frozen=True)
class FilterRule:
    """How one filter's standard magnitude is derived. The target's standard color
    relative to the comp is color_coef x its instrumental color difference. A direct
    filter adds coef x that color difference to its own differential magnitude; a
    `via` filter is the other filter's transformed magnitude minus (or plus) the
    target's standard color."""

    color: tuple[str, str]
    """Instrumental color used, bluer filter first, e.g. ('B', 'V')."""
    color_coef: str
    """Color transform, e.g. 'Tbv'."""
    coef: str
    """Coefficient named in the output notes; also scales the color error in MERR."""
    via: str | None = None


# TA's "AAVSO recommended" coefficient sets, keyed by the filters a session has.
TRANSFORM_SETS: dict[frozenset[str], tuple[str, dict[str, FilterRule]]] = {
    frozenset("BVR"): (
        "BVR",
        {
            "B": FilterRule(("B", "V"), "Tbv", "Tb_bv"),
            "V": FilterRule(("B", "V"), "Tbv", "Tv_bv"),
            "R": FilterRule(("V", "R"), "Tvr", "Tvr", via="V"),
        },
    ),
    frozenset("BV"): (
        "BV",
        {
            "B": FilterRule(("B", "V"), "Tbv", "Tb_bv"),
            "V": FilterRule(("B", "V"), "Tbv", "Tv_bv"),
        },
    ),
}


def transform_set(filters: list[str]) -> tuple[str, dict[str, FilterRule]] | None:
    return TRANSFORM_SETS.get(frozenset(filters))


def describe_set(rules: dict[str, FilterRule]) -> list[str]:
    """One human-readable line per filter, e.g. 'R: from V and (V-R) via Tvr'."""
    lines = []
    for filt, rule in rules.items():
        color = f"{rule.color[0]}-{rule.color[1]}"
        if rule.via:
            lines.append(f"{filt}: from {rule.via} and ({color}) via {rule.color_coef}")
        else:
            lines.append(f"{filt}: {rule.coef} × {rule.color_coef} on ({color})")
    return lines


def _needs(filt: str, rules: dict[str, FilterRule]) -> set[str]:
    rule = rules[filt]
    needed = {filt, *rule.color}
    if rule.via:
        needed |= _needs(rule.via, rules)
    return needed


def _standard_mag(filt, rules, d, cat, coeffs: TransformCoefficients):
    """Transformed magnitude, one per comp. `d` maps filter -> (target or check)
    instrumental magnitude minus each comp's; `cat` maps filter -> each comp's
    catalog magnitude. All arrays are over comps."""
    rule = rules[filt]
    c1, c2 = rule.color
    color_delta = coeffs.value(rule.color_coef) * (d[c1] - d[c2])
    if rule.via is None:
        return cat[filt] + d[filt] + coeffs.value(rule.coef) * color_delta
    base = _standard_mag(rule.via, rules, d, cat, coeffs)
    color = (cat[c1] - cat[c2]) + color_delta
    return base - color if filt == c2 else base + color


def _ta_merr(filt, rules, merr: dict[str, float], coeffs: TransformCoefficients) -> float:
    """TA's propagated uncertainty: the frame's own MERR (for a `via` filter, the base
    filter's) combined in quadrature with coef x the instrumental color's error."""
    rule = rules[filt]
    c1, c2 = rule.color
    anchor = merr[rule.via] if rule.via else merr[filt]
    return float(np.hypot(anchor, coeffs.value(rule.coef) * np.hypot(merr[c1], merr[c2])))


@dataclass
class TransformResult:
    report: AavsoReport
    set_name: str
    transformed: int
    skipped: list[str] = field(default_factory=list)
    """One reason per frame left untransformed (TRANS=NO), e.g. no partner frame."""


class TransformError(ValueError):
    pass


def _nearest(times: np.ndarray, index: np.ndarray, t: float) -> int | None:
    """Row index of the frame nearest time t (earlier wins a tie), within tolerance."""
    pos = np.searchsorted(times, t)
    best = None
    for p in (pos - 1, pos):
        if 0 <= p < len(times) and (best is None or abs(times[p] - t) < abs(times[best] - t)):
            best = p
    if best is None or abs(times[best] - t) > _PAIR_TOLERANCE_DAYS:
        return None
    return int(index[best])


def _check_delta(row: pd.Series) -> float:
    """Check-star instrumental magnitude minus the comp's. TA uses the instrumental
    KMAGINS - CMAGINS here (but MAG - CREFMAG for the target); with three-decimal
    rounding the two forms can differ by 0.001, so match TA's choice."""
    notes = _parse_notes(row["NOTES"])
    try:
        return float(notes["KMAGINS"]) - float(notes["CMAGINS"])
    except (KeyError, ValueError):
        return row["KMAGSTD"] - row["CREFMAG"]


def _fmt(value: float, decimals: int = 3) -> str:
    return f"{value:.{decimals}f}"


def untransformable_reason(report: AavsoReport) -> str | None:
    """Why this report can't be transformed, or None if it can."""
    if report.is_transformed:
        return "This report is already transformed."
    if report.is_ensemble:
        return (
            "Transforming an ensemble report isn't supported yet -- it needs a "
            "multi-filter ensemble session to build and verify against."
        )
    if transform_set(report.filters) is None:
        return (
            f"No transform set for filters {', '.join(report.filters)} -- "
            f"supported: {', '.join(name for name, _ in TRANSFORM_SETS.values())}."
        )
    return None


def transform_report(
    report: AavsoReport,
    coeffs: TransformCoefficients,
    auids: dict[str, str] | None = None,
) -> TransformResult:
    """Transform every frame of an untransformed single-comp report. `auids` maps
    chart labels to AUIDs (as TA does for CNAME/KNAME); labels are kept if absent."""
    reason = untransformable_reason(report)
    if reason:
        raise TransformError(reason)
    set_name, rules = transform_set(report.filters)
    df = report.df
    auids = auids or {}

    by_filter = {}
    for filt in rules:
        sub = df.loc[df["FILT"] == filt].sort_values("DATE", kind="stable")
        by_filter[filt] = (sub["DATE"].to_numpy(), sub.index.to_numpy())

    # One GROUP per cycle, numbered by the first filter's frames (B for BVR/BV).
    lead = next(iter(rules))
    group_of = {idx: f"{n + 1:04d}" for n, idx in enumerate(by_filter[lead][1])}

    out = df[STANDARD_COLUMNS].copy()
    out[ROW_COMMENTS_COLUMN] = [None] * len(out)
    skipped: list[str] = []
    transformed = 0
    for j, row in df.iterrows():
        filt = row["FILT"]
        partners = {f: (j if f == filt else _nearest(*by_filter[f], row["DATE"])) for f in _needs(filt, rules)}
        missing = sorted(f for f, p in partners.items() if p is None)
        when = f"{filt} @ {row['DATE']:.6f}"
        if missing:
            skipped.append(f"{when}: no {'/'.join(missing)} frame within {_PAIR_TOLERANCE_DAYS} d")
            continue
        rows = {f: df.loc[p] for f, p in partners.items()}
        if len({str(r["CNAME"]) for r in rows.values()}) > 1:
            skipped.append(f"{when}: paired frames use different comparison stars")
            continue

        cat = {f: np.array([r["CREFMAG"]]) for f, r in rows.items()}
        d_target = {f: np.array([r["MAG"] - r["CREFMAG"]]) for f, r in rows.items()}
        d_check = {f: np.array([_check_delta(r)]) for f, r in rows.items()}
        mag = float(np.mean(_standard_mag(filt, rules, d_target, cat, coeffs)))
        kmag = float(np.mean(_standard_mag(filt, rules, d_check, cat, coeffs)))
        if np.isnan(mag):
            skipped.append(f"{when}: missing MAG or CREFMAG")
            continue
        merr = _ta_merr(filt, rules, {f: r["MERR"] for f, r in rows.items()}, coeffs)

        coef = rules[filt].coef
        notes = _parse_notes(row["NOTES"])
        notes["VMAGSTD"] = _fmt(row["MAG"])
        notes["VERR"] = _fmt(row["MERR"])
        if not np.isnan(kmag):
            notes["KMAGTRAN"] = _fmt(kmag)
        notes[coef] = _fmt(coeffs.value(coef), 4)
        if coeffs.error(coef) is not None:
            notes[f"{coef}Err"] = _fmt(coeffs.error(coef), 4)

        out.at[j, "MAG"] = round(mag, 3)
        out.at[j, "MERR"] = round(merr, 3)
        out.at[j, "TRANS"] = "YES"
        out.at[j, "GROUP"] = group_of[partners[lead]]
        out.at[j, "CNAME"] = auids.get(str(row["CNAME"]), row["CNAME"])
        out.at[j, "KNAME"] = auids.get(str(row["KNAME"]), row["KNAME"])
        out.at[j, "NOTES"] = "|" + "|".join(f"{k}={notes[k]}" for k in sorted(notes))
        using = ", ".join(f"{f} @ {df.at[partners[f], 'DATE']:.6f}" for f in rules if f in partners)
        out.at[j, ROW_COMMENTS_COLUMN] = [
            "#" + format_row(row, report.header.get("DELIM", ",")),
            f"# {set_name} transform using: {using}",
        ]
        transformed += 1

    comments = [f"#  AAVSO Report Viewer transform ({set_name}), coefficients from {coeffs.source}"]
    comments += coeffs.header_lines()
    new_report = report_from_frame(out, header=report.header, comments=comments, source_path=report.source_path)
    return TransformResult(new_report, set_name, transformed, skipped)
