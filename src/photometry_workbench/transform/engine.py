"""Apply TG transform coefficients to an untransformed AAVSO report, reproducing
AAVSO TransformApplier (TA)'s output for single-comparison-star sessions.

The math works per comparison star: each frame's target and check magnitudes are
transformed differentially against one comp star at a time. It's linear, so for a
VPhot ensemble -- an unweighted average of its comps, whose average instrumental and
catalog magnitudes the report carries as CMAGINS/CREFMAG -- transforming against that
average comp gives the same result as transforming against each comp and averaging.
So transform_report treats every report as single-comp. comp_estimates does the
per-comp version, from the General Report, for the spread across comps.

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

from photometry_workbench import APP_NAME
from photometry_workbench.parsing.aavso_report import (
    STANDARD_COLUMNS,
    AavsoReport,
    _parse_notes,
    report_from_frame,
)
from photometry_workbench.parsing.aavso_writer import ROW_COMMENTS_COLUMN, format_row
from photometry_workbench.transform.coefficients import TransformCoefficients

# Farthest apart (in days, ~14 min) two frames in different filters can be and still
# be paired into one transform group. Normal cycles are a minute or two apart; this
# only stops a frame from being paired across a long gap.
_PAIR_TOLERANCE_DAYS = 0.01

# A General Report row and the AAVSO report row for the same exposure carry JDs
# computed independently that can differ by a few 1e-5 d (~8.6 s; same tolerance as
# Session's General Report join).
_GENERAL_JOIN_TOLERANCE_DAYS = 0.0001


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


def _nearest(times: np.ndarray, index: np.ndarray, t: float, tolerance: float = _PAIR_TOLERANCE_DAYS) -> int | None:
    """Row index of the frame nearest time t (earlier wins a tie), within tolerance."""
    pos = np.searchsorted(times, t)
    best = None
    for p in (pos - 1, pos):
        if 0 <= p < len(times) and (best is None or abs(times[p] - t) < abs(times[best] - t)):
            best = p
    if best is None or abs(times[best] - t) > tolerance:
        return None
    return int(index[best])


def _sorted_by_filter(df: pd.DataFrame, filters, filter_col: str, date_col: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    result = {}
    for filt in filters:
        sub = df.loc[df[filter_col] == filt].sort_values(date_col, kind="stable")
        result[filt] = (sub[date_col].to_numpy(), sub.index.to_numpy())
    return result


def _pair_frames(df: pd.DataFrame, rules: dict[str, FilterRule]) -> tuple[dict, dict]:
    """For each row: {filter: partner row index or None} over the filters its
    transform needs (the row itself for its own filter). Also one GROUP id per cycle,
    numbered by the first filter's frames (B for BVR/BV), keyed by that frame's index."""
    by_filter = _sorted_by_filter(df, rules, "FILT", "DATE")
    lead = next(iter(rules))
    group_of = {idx: f"{n + 1:04d}" for n, idx in enumerate(by_filter[lead][1])}
    partners_of = {}
    for j, row in df.iterrows():
        filt = row["FILT"]
        if filt not in rules:
            partners_of[j] = None
            continue
        partners_of[j] = {
            f: (j if f == filt else _nearest(*by_filter[f], row["DATE"])) for f in _needs(filt, rules)
        }
    return partners_of, group_of


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
    if transform_set(report.filters) is None:
        return (
            f"The transform needs filters it has coefficients for "
            f"({' or '.join(name for name, _ in TRANSFORM_SETS.values())}); this session has "
            f"{', '.join(report.filters)} only."
        )
    return None


def ensemble_membership_problem(general: pd.DataFrame, comps: list[str], filters: list[str]) -> str | None:
    """VPhot averages, per filter, whichever comps it measured in that filter. If a
    comp is missing from some filters, the 'average comp' is a different set of stars
    in each, its color is meaningless, and the transform would be wrong."""
    measured = {
        comp: {f for f in filters if general.loc[general["Filter"] == f, comp].notna().any()}
        for comp in comps
        if comp in general.columns
    }
    partial = {c: sorted(set(filters) - fs) for c, fs in measured.items() if fs and fs != set(filters)}
    if not partial:
        return None
    detail = "; ".join(f"{c} has no {'/'.join(m)}" for c, m in partial.items())
    return (
        f"The ensemble's comparison stars aren't all measured in every filter ({detail}), so it "
        "can't be transformed as one average comp. Re-run the ensemble in VPhot with comps that "
        "have catalog magnitudes in all filters."
    )


def transform_report(
    report: AavsoReport,
    coeffs: TransformCoefficients,
    auids: dict[str, str] | None = None,
    ensemble_members: list[str] | None = None,
    rounded: bool = True,
) -> TransformResult:
    """Transform every frame of an untransformed report (single comp, or an ensemble
    as its average comp). `auids` maps chart labels to AUIDs (as TA does for
    CNAME/KNAME); labels are kept if absent. `ensemble_members` (comp labels, known
    only from the General Report) is listed in NOTES, as AAVSO asks for ensembles.
    `rounded=False` keeps MAG unrounded, for comparing transforms that differ by
    less than the report's 0.001 resolution."""
    reason = untransformable_reason(report)
    if reason:
        raise TransformError(reason)
    set_name, rules = transform_set(report.filters)
    df = report.df
    auids = auids or {}
    partners_of, group_of = _pair_frames(df, rules)
    lead = next(iter(rules))

    out = df[STANDARD_COLUMNS].copy()
    out[ROW_COMMENTS_COLUMN] = [None] * len(out)
    skipped: list[str] = []
    transformed = 0
    for j, row in df.iterrows():
        filt = row["FILT"]
        partners = partners_of[j]
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
        if report.is_ensemble and ensemble_members:
            notes["ENSCOMPS"] = ";".join(ensemble_members)

        out.at[j, "MAG"] = round(mag, 3) if rounded else mag
        out.at[j, "MERR"] = round(merr, 3)
        out.at[j, "TRANS"] = "YES"
        out.at[j, "GROUP"] = group_of[partners[lead]]
        out.at[j, "CNAME"] = auids.get(str(row["CNAME"]), row["CNAME"])
        out.at[j, "KNAME"] = auids.get(str(row["KNAME"]), row["KNAME"])
        if report.is_ensemble and not np.isnan(kmag):
            # An ensemble report's KMAG is the check star's calculated magnitude, not
            # an instrumental one (AAVSO Extended format spec).
            out.at[j, "KMAG"] = round(kmag, 3)
        out.at[j, "NOTES"] = "|" + "|".join(f"{k}={notes[k]}" for k in sorted(notes))
        using = ", ".join(f"{f} @ {df.at[partners[f], 'DATE']:.6f}" for f in rules if f in partners)
        out.at[j, ROW_COMMENTS_COLUMN] = [
            "#" + format_row(row, report.header.get("DELIM", ",")),
            f"# {set_name} transform using: {using}",
        ]
        transformed += 1

    comments = [f"#  {APP_NAME} transform ({set_name}), coefficients from {coeffs.source}"]
    comments += coeffs.header_lines()
    new_report = report_from_frame(out, header=report.header, comments=comments, source_path=report.source_path)
    return TransformResult(new_report, set_name, transformed, skipped)


def _instrumental_difference(row: pd.Series, target: str, used: list[str], cat: np.ndarray, comps: list[str]) -> np.ndarray:
    """Target minus each used comp's instrumental magnitude, from one General Report row.

    The target column T is measured against all n comps VPhot used in this row, but
    comp i's column is measured against the other n-1, so T - col_i is off from the
    true difference by (col_i - cat_i)/n -- comp i's own offset from catalog as the
    others see it, shared out over the ensemble. The errors sum to zero over the comps
    (the ensemble mean is unaffected) but spread each comp's estimate out by n/(n-1)
    if left in."""
    n = sum(pd.notna(row.get(c)) for c in comps)
    col = np.array([row[c] for c in used])
    return (row[target] - col) + (col - cat) / n


def per_comp_estimates(
    report: AavsoReport,
    general: pd.DataFrame,
    comps: list[str],
    catalog: dict[str, dict[str, object]],
    coeffs: TransformCoefficients | None = None,
) -> pd.DataFrame:
    """For each frame of an (untransformed) ensemble report, the target's magnitude
    estimated against each comp separately -- transformed with `coeffs` when given.
    One column per comp, indexed like report.df; NaN where a comp wasn't used.
    `catalog` is {label: {filter: ChartMag}} (Session.chart_photometry()).

    Each comp's General Report column is its magnitude measured against the *other*
    comps (leave-one-out), not the whole ensemble -- verified against single-comp
    reports of the same frames (tests/test_ensemble.py). See _instrumental_difference
    for how the target-minus-comp instrumental difference is recovered from it. A comp
    is used for a frame only if it has a measurement and a catalog magnitude in every
    filter that frame's transform needs."""
    df = report.df
    target = report.star_name
    if target not in general.columns:
        raise ValueError(f"The General Report has no column for {target}.")
    rules = transform_set(report.filters)[1] if coeffs is not None else None
    partners_of = _pair_frames(df, rules)[0] if rules else {j: {f: j} for j, f in df["FILT"].items()}
    general_rows = _sorted_by_filter(general, report.filters, "Filter", "JD")

    estimates = pd.DataFrame(np.nan, index=df.index, columns=comps)
    for j, partners in partners_of.items():
        if not partners or any(p is None for p in partners.values()):
            continue
        g = {}
        for f, p in partners.items():
            k = _nearest(*general_rows[f], df.at[p, "DATE"], _GENERAL_JOIN_TOLERANCE_DAYS)
            if k is None:
                break
            g[f] = general.loc[k]
        else:
            used = [
                c for c in comps
                if all(pd.notna(g[f].get(c)) and f in catalog.get(c, {}) for f in partners)
            ]
            if not used:
                continue
            cat = {f: np.array([catalog[c][f].mag for c in used]) for f in partners}
            d = {f: _instrumental_difference(g[f], target, used, cat[f], comps) for f in partners}
            filt = df.at[j, "FILT"]
            est = _standard_mag(filt, rules, d, cat, coeffs) if rules else cat[filt] + d[filt]
            estimates.loc[j, used] = est
    return estimates


def comp_estimates(
    report: AavsoReport,
    general: pd.DataFrame,
    comps: list[str],
    catalog: dict[str, dict[str, object]],
    coeffs: TransformCoefficients | None = None,
) -> pd.DataFrame:
    """per_comp_estimates summarized per frame as columns n (comps used), mean and
    std (ddof=1, NaN with fewer than 2 comps), indexed like report.df."""
    estimates = per_comp_estimates(report, general, comps, catalog, coeffs)
    return pd.DataFrame(
        {"n": estimates.notna().sum(axis=1), "mean": estimates.mean(axis=1), "std": estimates.std(axis=1, ddof=1)}
    )


# The per-comp estimates averaged (method B) should reproduce the report's ensemble
# magnitude (method A) to within the General Report's 0.001 rounding (<= 0.0008 seen on
# real sessions); a bigger gap means the two disagree about the data.
CROSS_CHECK_LIMIT = 0.005


def ensemble_cross_check(mag: pd.Series, spread: pd.DataFrame, n_comps: int) -> str | None:
    """Compare the report's ensemble magnitudes (`mag`, the transform of the average
    comp) with the per-comp estimates averaged (`spread`, comp_estimates() indexed like
    `mag`). Returns a warning, or None if they agree and every frame used every comp.

    A comp missing from some frames (a dropout) means the "average comp" is a different
    set of stars in those frames than in the frames they're paired with for color, so
    those frames' transform is suspect even when the averages happen to agree."""
    spread = spread.reindex(mag.index)
    problems = []
    gap = (spread["mean"] - mag).abs()
    off = int((gap > CROSS_CHECK_LIMIT).sum())
    if off:
        problems.append(
            f"on {off} frame(s) the per-comp estimates average up to {gap.max():.3f} mag away from the "
            "report's magnitude, so the General Report or catalog may not match the ensemble VPhot used"
        )
    short = int((spread["n"].fillna(0) < n_comps).sum())
    if short:
        problems.append(
            f"{short} frame(s) used fewer than all {n_comps} comparison stars, so their average comp "
            "differs from the frames they're paired with"
        )
    if not problems:
        return None
    return "Ensemble cross-check: " + "; ".join(problems) + "."


@dataclass(frozen=True)
class CoefficientSensitivity:
    """How far one filter's transformed target magnitude moves when the transform
    coefficients move by their own errors -- a systematic uncertainty (the same
    coefficients bias every frame alike), so it's reported beside MERR, not in it."""

    total: float
    """The per-coefficient shifts combined in quadrature."""
    terms: dict[str, float]
    """Coefficient name -> |shift| of the filter's mean magnitude when that coefficient
    alone moves by ± its error (the larger of the two directions)."""


def coefficient_sensitivity(report: AavsoReport, coeffs: TransformCoefficients) -> dict[str, CoefficientSensitivity]:
    """Per filter, the shift in the mean transformed target magnitude from moving each
    coefficient the transform uses by ± its error, one at a time (a check suggested by an
    AAVSO mentor). The shift scales with the target-minus-comp color, so it's
    small for color-matched comps and grows for a red target against blue comps.
    Coefficients with no error are skipped; empty if none have one."""
    _, rules = transform_set(report.filters)
    names = sorted({rule.color_coef for rule in rules.values()} | {rule.coef for rule in rules.values()})
    names = [n for n in names if coeffs.error(n)]
    if not names:
        return {}

    def mean_mags(c: TransformCoefficients) -> pd.Series:
        df = transform_report(report, c, rounded=False).report.df
        return df.loc[df["TRANS"] == "YES"].groupby("FILT")["MAG"].mean()

    base = mean_mags(coeffs)
    terms: dict[str, dict[str, float]] = {f: {} for f in base.index}
    for name in names:
        shifts = []
        for sign in (1, -1):
            values = dict(coeffs.values)
            values[name] += sign * coeffs.error(name)
            shifted = TransformCoefficients(values, coeffs.errors, coeffs.description, coeffs.source)
            shifts.append((mean_mags(shifted) - base).abs())
        biggest = pd.concat(shifts, axis=1).max(axis=1)
        for filt in terms:
            terms[filt][name] = float(biggest.get(filt, 0.0))
    return {
        filt: CoefficientSensitivity(float(np.sqrt(sum(s**2 for s in t.values()))), t)
        for filt, t in terms.items()
    }
