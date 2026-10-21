"""Shared accuracy/precision coloring, with thresholds from experienced AAVSO observers'
guidance -- one rule per quantity, used everywhere it's shown.

Color flags something worth a look; it isn't a grade. Values in the normal range stay
uncolored rather than red, so a submittable session doesn't read as broken. Each rule
has a matching *_tip() giving the reason, shown as the cell's tooltip.
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


def ratio_tip(value: float | None, reference: float | None) -> str | None:
    """Why ratio_color colored an offset from catalog (`reference` = the catalog error)."""
    color = ratio_color(value, reference)
    if color is None:
        return None
    if color == GOOD:
        return f"Within the catalog error: off by {value:+.3f}, catalog error ±{reference:.3f}."
    times = "more than" if color == BORDERLINE else "more than twice"
    return f"Off catalog by {value:+.3f}, {times} the catalog error (±{reference:.3f})."


# Next steps for a check star off by more than its catalog error (appended to ratio_tip).
CHECK_TRY_TRANSFORM = (
    "Applying the transform (Transform → Apply Transform) often brings this within the "
    "catalog error, especially in B."
)
CHECK_STILL_OUTSIDE = (
    "Still outside after the transform: check the coefficients, the chart's catalog values, "
    "or the comparison star."
)


# Precision guide for a check or comparison star's scatter over the night. Real
# sessions typically land at 0.01-0.03 mag (well above the photon-noise floor, since
# real scatter is dominated by other noise), and that's submittable, so it isn't
# colored. Only scatter well beyond that is flagged: clouds, bad frames, or a problem
# with the star itself.
STD_POOR = 0.05


def std_color(std: float | None) -> QColor | None:
    if std is None or std <= STD_POOR:
        return None
    return BORDERLINE


def std_tip(std: float | None) -> str | None:
    if std_color(std) is None:
        return None
    return (
        f"Scatter of {std:.3f} mag is unusually large (typical is 0.01-0.03). Look for clouds, "
        "bad frames, or a problem with this star."
    )


# Extinction/flatness-over-time guide: a common rule of thumb is that atmospheric
# extinction is usually <~0.05 mag for a time series with a significant airmass
# swing -- used to color a check star's magnitude-vs-airmass trend.
TREND_LIMIT = 0.05


def trend_color(trend: float | None) -> QColor | None:
    if trend is None:
        return None
    return GOOD if abs(trend) <= TREND_LIMIT else FLAG


# VSX range tolerance: VSX's published range is just the observed min/max in the
# database, not authoritative, so small overshoots are normal; so is falling short of
# it, when a session doesn't cover a full cycle. Only an overshoot beyond this is
# flagged, since it can mean outlier frames.
RANGE_TOLERANCE = 0.1


def range_color(excess: float | None) -> QColor | None:
    """`excess` is how far outside the VSX range the observed range falls (0 or less
    if fully inside, None if there's no VSX comparison available)."""
    if excess is None or excess <= RANGE_TOLERANCE:
        return None
    return BORDERLINE


def range_tip(excess: float | None) -> str | None:
    if range_color(excess) is None:
        return None
    return (
        f"Goes {excess:.2f} mag beyond the VSX range. Small overshoots are normal, but this "
        "much can mean outlier frames, or a genuinely new extreme."
    )


# Before/after improvement guide for color indices: catalog color errors (~0.1 mag)
# swamp typical deltas, so judging against them turns everything green. Instead judge
# whether the transform moved the measured color closer to catalog. A change within
# 0.005 (about the best consistency a small telescope achieves) is too small to call
# either way. Getting worse is only flagged once the result also falls outside the
# catalog error -- worse-but-still-within-catalog-error is borderline, not a flag.
IMPROVEMENT_TOLERANCE = 0.005


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


def improvement_tip(
    delta_before: float | None, delta_after: float | None, catalog_err: float | None = None
) -> str | None:
    color = improvement_color(delta_before, delta_after, catalog_err)
    if color is None:
        return None
    moved = f"({delta_before:+.3f} before, {delta_after:+.3f} after)"
    if color == GOOD:
        return f"The transform moved this color closer to catalog {moved}."
    if abs(abs(delta_before) - abs(delta_after)) <= IMPROVEMENT_TOLERANCE:
        return f"The transform barely changed this color {moved}."
    if color == BORDERLINE:
        return f"The transform moved this color slightly away from catalog, but it's still within the catalog error {moved}."
    return f"The transform moved this color away from catalog, and outside the catalog error {moved}."


def consistency_color(ratio: float | None) -> QColor | None:
    """The Std across frames vs the single-frame uncertainty (submission.builder.
    frame_consistency): fine below 1.5x, flagged at or above it."""
    from photometry_workbench.submission.builder import CONSISTENCY_LIMIT

    if ratio is None or ratio != ratio:
        return None
    return GOOD if ratio < CONSISTENCY_LIMIT else FLAG
