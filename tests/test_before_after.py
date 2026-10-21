import pytest

from photometry_workbench.models.session import Session
from photometry_workbench.parsing.aavso_report import parse_aavso_report
from photometry_workbench.parsing.general_report import parse_general_report
from photometry_workbench.ui import coloring

from test_parsing import FIXTURES


def _cy_aqr_before_after_session() -> Session:
    before = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    after = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_TA.txt")
    general = parse_general_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_General.txt")
    session = Session(before, general)
    session.attach_after_report(after)
    return session


def test_before_after_matches_notebook_reference_values():
    """Golden regression test: these exact numbers were independently verified
    against aavso_report_check.ipynb's own output for this same real session."""
    session = _cy_aqr_before_after_session()
    check_rows = {r.filt: r for r in session.build_summary_rows() if r.section == "Check stars"}

    v = check_rows["V"]
    assert v.average == pytest.approx(11.754, abs=1e-3)
    assert v.delta == pytest.approx(0.014, abs=1e-3)
    assert v.average_after == pytest.approx(11.766, abs=1e-3)
    assert v.delta_after == pytest.approx(0.026, abs=1e-3)
    assert v.kreferr == pytest.approx(0.100, abs=1e-3)

    b = check_rows["B"]
    assert b.delta == pytest.approx(0.187, abs=1e-3)
    assert b.delta_after == pytest.approx(0.063, abs=1e-3)
    assert b.kreferr == pytest.approx(0.101, abs=1e-3)

    r = check_rows["R"]
    assert r.delta == pytest.approx(-0.038, abs=1e-3)
    assert r.delta_after == pytest.approx(0.006, abs=1e-3)


def test_noise_floor_and_trend_populated_for_check_stars_only():
    session = _cy_aqr_before_after_session()
    rows = session.build_summary_rows()

    for r in rows:
        if r.section == "Check stars":
            assert r.noise_floor is not None
            assert r.trend is not None
            assert r.trend_after is not None
        else:
            assert r.noise_floor is None
            assert r.trend is None


def test_target_range_after_and_vsx_excess(monkeypatch):
    monkeypatch.setattr(
        "photometry_workbench.models.session.fetch_vsx_range", lambda star_name: (10.5, 11.0)
    )
    session = _cy_aqr_before_after_session()
    target_rows = {r.filt: r for r in session.build_summary_rows() if r.section == "Targets"}

    v = target_rows["V"]
    assert v.vsx_bright == 10.5
    assert v.vsx_faint == 11.0
    assert v.range_after is not None  # after report has V data too
    # observed [10.686, 11.058] fully inside [10.5, 11.0]? max 11.058 > faint 11.0 -> excess
    assert v.vsx_excess == pytest.approx(max(0.0, v.maximum - 11.0), abs=1e-6)

    b = target_rows["B"]
    assert b.vsx_bright is None  # VSX only publishes V-band amplitude
    assert b.vsx_excess is None


def test_single_report_mode_has_no_after_fields():
    """Without an after report, all the new before/after fields stay None -- no crash,
    matches increment 1's existing single-report behavior."""
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    general = parse_general_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_General.txt")
    session = Session(report, general)
    assert session.has_after_report is False

    for r in session.build_summary_rows():
        assert r.average_after is None
        assert r.std_after is None
        assert r.trend_after is None
        assert r.range_after is None
        if r.section == "Check stars":
            assert r.noise_floor is not None  # still works without an after report
            assert r.trend is not None


def test_coloring_thresholds():
    assert coloring.ratio_color(0.05, 0.1) == coloring.GOOD
    assert coloring.ratio_color(0.15, 0.1) == coloring.BORDERLINE
    assert coloring.ratio_color(0.25, 0.1) == coloring.FLAG
    assert coloring.ratio_color(None, 0.1) is None
    assert coloring.ratio_color(0.05, None) is None
    assert coloring.ratio_color(0.05, 0) is None

    assert coloring.trend_color(0.03) == coloring.GOOD
    assert coloring.trend_color(-0.06) == coloring.FLAG
    assert coloring.trend_color(None) is None

    # Only scatter well beyond the typical 0.01-0.03 is flagged; normal values stay plain.
    assert coloring.std_color(0.003) is None
    assert coloring.std_color(0.03) is None
    assert coloring.std_color(0.06) == coloring.BORDERLINE
    assert coloring.std_color(None) is None
    assert coloring.std_tip(0.03) is None and "unusually large" in coloring.std_tip(0.06)

    # Falling short of, or slightly past, the VSX range is normal; only a big overshoot flags.
    assert coloring.range_color(-0.01) is None
    assert coloring.range_color(0.05) is None
    assert coloring.range_color(0.2) == coloring.BORDERLINE
    assert coloring.range_color(None) is None
    assert coloring.range_tip(0.05) is None and "beyond the VSX range" in coloring.range_tip(0.2)

    assert "more than twice" in coloring.ratio_tip(0.25, 0.1)
    assert coloring.ratio_tip(0.05, None) is None
