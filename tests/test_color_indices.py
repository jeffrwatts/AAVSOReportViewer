import pytest

from photometry_workbench.models.session import Session
from photometry_workbench.parsing.aavso_report import parse_aavso_report

from test_before_after import _cy_aqr_before_after_session
from test_parsing import FIXTURES


def test_check_star_color_indices_before_after():
    """CY Aqr's check star 117: the TA transform should pull B-V much closer to
    catalog -- ΔB-V is just ΔB - ΔV from the golden per-filter test (0.187 - 0.014)."""
    rows = {c.index: c for c in _cy_aqr_before_after_session().build_color_rows()}

    # Adjacent bands only: B-V and V-R, no redundant B-R.
    assert set(rows) == {"B-V", "V-R"}

    bv = rows["B-V"]
    assert bv.section == "Check stars"
    assert bv.star_label == "117"
    assert bv.catalog == pytest.approx(12.217 - 11.740, abs=1e-6)
    assert bv.catalog_err == pytest.approx((0.101**2 + 0.100**2) ** 0.5, abs=1e-6)
    assert bv.delta == pytest.approx(0.173, abs=2e-3)
    assert bv.delta_after == pytest.approx(0.037, abs=2e-3)

    vr = rows["V-R"]
    assert vr.delta == pytest.approx(0.051, abs=2e-3)
    assert vr.delta_after == pytest.approx(0.020, abs=2e-3)


def test_single_filter_session_has_no_color_indices():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    report.df = report.df.loc[report.df["FILT"] == "V"]
    report.filters = ["V"]
    assert Session(report).build_color_rows() == []


def test_improvement_color():
    from photometry_workbench.ui import coloring

    assert coloring.improvement_color(0.174, 0.037) == coloring.GOOD
    # Overshooting past catalog still counts if it lands closer.
    assert coloring.improvement_color(0.052, -0.031) == coloring.GOOD
    # Within the tolerance either way: no meaningful change.
    assert coloring.improvement_color(0.053, 0.056) == coloring.BORDERLINE
    assert coloring.improvement_color(0.010, -0.020) == coloring.FLAG
    # Worse, but still within catalog error: borderline rather than a flag.
    assert coloring.improvement_color(0.010, -0.020, catalog_err=0.121) == coloring.BORDERLINE
    assert coloring.improvement_color(0.010, -0.150, catalog_err=0.121) == coloring.FLAG
    assert coloring.improvement_color(None, 0.01) is None
