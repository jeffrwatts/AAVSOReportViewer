"""Shared 3-tier accuracy/precision coloring, thresholds confirmed directly by Jeff's
AAVSO mentor -- one coloring rule used everywhere (Average deltas, Std, comparison-star
deltas, Trend, VSX range), not a different rule per table.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

GOOD = QColor("#2e7d32")
BORDERLINE = QColor("#ed6c02")
FLAG = QColor("#d32f2f")


def ratio_color(value: float | None, reference: float | None) -> QColor | None:
    """good if |value|/reference <= 1x, borderline <= 2x, else flag. None (uncolored)
    if either input is missing or reference is zero -- nothing to judge against."""
    if value is None or reference is None or reference == 0:
        return None
    ratio = abs(value) / reference
    if ratio <= 1:
        return GOOD
    if ratio <= 2:
        return BORDERLINE
    return FLAG


# Precision guide, per Jeff's AAVSO mentor directly (not a ratio to the photon-noise
# floor, which he called merely "a quick way to get a feel" -- not a hard threshold,
# and one that flagged essentially every real session red since actual scatter is
# structurally dominated by non-photon noise, not achievable photon-limited precision).
# His actual numbers: "the usual guide is to make measurements so that statistical
# error is < 0.01 mag for the variable, comparison and check stars" (STD_ACCEPTABLE),
# and "with your equipment, you are not likely to get much better than 0.005 mag
# consistency" (STD_GOOD) -- so applies to all three star roles, not just check stars.
STD_GOOD = 0.005
STD_ACCEPTABLE = 0.01


def std_color(std: float | None) -> QColor | None:
    if std is None:
        return None
    if std <= STD_GOOD:
        return GOOD
    if std <= STD_ACCEPTABLE:
        return BORDERLINE
    return FLAG


# Extinction/flatness-over-time guide: mentor's rule of thumb is atmospheric
# extinction is usually <~0.05 mag for a time series with a significant airmass
# swing -- used to color a check star's magnitude-vs-airmass trend.
TREND_LIMIT = 0.05


def trend_color(trend: float | None) -> QColor | None:
    if trend is None:
        return None
    return GOOD if abs(trend) <= TREND_LIMIT else FLAG


# VSX range tolerance: per the mentor, don't fuss over VSX range differences under
# ~0.1 mag -- VSX's published range is just the observed min/max in the database, not
# authoritative. A small overshoot beyond either edge is borderline, not a flag.
RANGE_TOLERANCE = 0.1


def range_color(excess: float | None) -> QColor | None:
    """`excess` is how far outside the VSX range the observed range falls (0 or less
    if fully inside, None if there's no VSX comparison available)."""
    if excess is None:
        return None
    if excess <= 0:
        return GOOD
    if excess <= RANGE_TOLERANCE:
        return BORDERLINE
    return FLAG


# Before/after improvement guide for color indices: catalog color errors (~0.1 mag)
# swamp typical deltas, so judging against them turns everything green. Instead judge
# whether the transform moved the measured color closer to catalog. A change within
# STD_GOOD (the best consistency the mentor expects from Jeff's equipment) is too small
# to call either way. Getting worse is only flagged once the result also falls outside
# the catalog error -- worse-but-still-within-catalog-error is borderline, not a flag.
IMPROVEMENT_TOLERANCE = STD_GOOD


def improvement_color(
    delta_before: float | None, delta_after: float | None, catalog_err: float | None = None
) -> QColor | None:
    if delta_before is None or delta_after is None:
        return None
    change = abs(delta_before) - abs(delta_after)
    if change > IMPROVEMENT_TOLERANCE:
        return GOOD
    if change >= -IMPROVEMENT_TOLERANCE:
        return BORDERLINE
    if catalog_err is not None and abs(delta_after) <= catalog_err:
        return BORDERLINE
    return FLAG


def consistency_color(ratio: float | None) -> QColor | None:
    """The Std across frames vs the single-frame uncertainty (submission.builder.
    frame_consistency): fine below the mentor's 1.5x, flagged at or above it."""
    from aavso_viewer.submission.builder import CONSISTENCY_LIMIT

    if ratio is None or ratio != ratio:
        return None
    return GOOD if ratio < CONSISTENCY_LIMIT else FLAG
