from pathlib import Path

import pytest

from photometry_workbench.models.session import Session
from photometry_workbench.parsing.aavso_report import parse_aavso_report
from photometry_workbench.parsing.general_report import parse_general_report

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_single_comp_untransformed():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    assert report.star_name == "CY Aqr"
    assert report.is_transformed is False
    assert report.is_ensemble is False
    assert set(report.filters) == {"V", "B", "R"}
    assert len(report.df) > 0


def test_parse_transformed_report():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_TA.txt")
    assert report.is_transformed is True
    assert report.is_ensemble is False
    # KEFF should prefer KMAGTRAN over KMAGSTD on transformed rows
    trans_rows = report.df[report.df["TRANS"] == "YES"]
    assert trans_rows["KEFF"].equals(trans_rows["KMAGTRAN"])


def test_parse_general_report_column_detection():
    general = parse_general_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_General.txt")
    assert set(general.diagnostic_columns) == {"Err", "SNR", "FWHM", "Skyglow", "Max ADU", "I.M."}
    assert set(general.star_columns) == {"CY Aqr", "117", "113"}


def test_session_summary_rows_without_general_report():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    session = Session(report)
    rows = session.build_summary_rows()

    target_rows = [r for r in rows if r.section == "Targets"]
    assert {r.filt for r in target_rows} == {"V", "B", "R"}
    for r in target_rows:
        assert r.average is not None
        assert r.avg_snr is None
        assert r.avg_snr_available is True  # renders "*", clickable

    check_rows = [r for r in rows if r.section == "Check stars"]
    for r in check_rows:
        assert r.average is not None
        assert r.avg_snr_available is False  # renders "-", never derivable

    comp_rows = [r for r in rows if r.section == "Comparison stars"]
    for r in comp_rows:
        assert r.average is None
        assert r.avg_snr_available is False
        # CY Aqr is single-comparison-star, not ensemble -- comp stats are
        # permanently unavailable (renders "-"), not unlocked by a General Report.
        assert r.needs_general_report is False


def test_session_summary_rows_with_general_report():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    general = parse_general_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_General.txt")
    session = Session(report, general)
    rows = session.build_summary_rows()

    target_rows = [r for r in rows if r.section == "Targets"]
    for r in target_rows:
        assert r.avg_snr is not None
        assert r.avg_snr > 0

    # CY Aqr is a single-comparison-star session: VPhot's General Export puts a
    # literal 'na' placeholder in the comp star's own magnitude column (it's the
    # calibration anchor, not something tracked per-frame), so stats stay permanently
    # "-" even with a General Report loaded -- unlike an ensemble session, this isn't
    # something loading a (different) General Report would ever unlock.
    comp_rows = [r for r in rows if r.section == "Comparison stars"]
    for r in comp_rows:
        assert r.star_label == "113"
        assert r.average is None
        assert r.needs_general_report is False


def test_target_check_light_curve():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    session = Session(report)
    curve = session.target_check_light_curve()
    assert set(curve["role"].unique()) == {"target", "check"}
    assert curve["mag"].notna().all()


def test_comp_star_names_empty_without_general_report():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    session = Session(report)
    assert session.comp_star_names() == []


def test_diagnostic_series_and_airmass():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    general = parse_general_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_General.txt")
    session = Session(report, general)

    snr = session.diagnostic_series("SNR")
    assert not snr.empty
    assert snr["value"].notna().any()

    airmass = session.airmass_series()
    assert airmass["value"].notna().all()


def test_session_date_and_metadata():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    session = Session(report)
    assert session.star_name == "CY Aqr"
    assert session.session_date is not None
    assert session.is_ensemble is False
    assert session.is_transformed is False


def test_star_label_and_delta_against_catalog():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823.txt")
    session = Session(report)
    rows = session.build_summary_rows()

    target_rows = [r for r in rows if r.section == "Targets"]
    for r in target_rows:
        assert r.star_label == "CY Aqr"
        assert r.catalog_mag is None
        assert r.delta is None  # no catalog magnitude to compare against
        assert r.kreferr is None

    check_rows = [r for r in rows if r.section == "Check stars"]
    for r in check_rows:
        assert r.star_label == "117"
        assert r.catalog_mag is not None
        assert r.kreferr is not None
        assert r.delta == pytest.approx(r.average - r.catalog_mag)

    comp_rows = [r for r in rows if r.section == "Comparison stars"]
    for r in comp_rows:
        assert r.star_label == "113"
        assert r.catalog_mag is not None  # CREFMAG is known even though stats are "-"
        assert r.average is None


def test_ensemble_comp_stats_need_general_report():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_YZ-Boo_Ensemble_Synthetic.txt")
    session = Session(report)  # no General Report attached yet

    comp_rows = [r for r in session.build_summary_rows() if r.section == "Comparison stars"]
    for r in comp_rows:
        assert r.star_label == "ENSEMBLE"
        assert r.average is None
        assert r.needs_general_report is True  # renders "*", clickable -- unlike single-comp


def test_ensemble_detection_and_comp_star_curves(monkeypatch):
    # Chart-photometry lookup hits a real network API -- stub it out so this test
    # stays fast and offline-safe (the fetch/parse logic itself is unit-tested in
    # test_fetch_chart_photometry_parses_response, and verified against the live
    # API manually).
    monkeypatch.setattr(
        "photometry_workbench.models.session.fetch_chart_photometry", lambda chart_id: {}
    )

    report = parse_aavso_report(FIXTURES / "AAVSOReport_YZ-Boo_Ensemble_Synthetic.txt")
    assert report.is_ensemble is True

    general = parse_general_report(FIXTURES / "YZ+Boo_General.txt")
    session = Session(report, general)

    assert set(session.comp_star_names()) == {"103", "108", "114", "118", "119"}

    # With the General Report loaded, each named comparison star gets its own row
    # with real stats (from its instrumental-mag column), not a single collapsed
    # "ENSEMBLE" placeholder row.
    comp_rows = [r for r in session.build_summary_rows() if r.section == "Comparison stars"]
    assert {r.star_label for r in comp_rows} == {"103", "108", "114", "118", "119"}
    for r in comp_rows:
        assert r.average is not None
        assert r.needs_general_report is False

    curve = session.comp_stars_light_curve()
    assert set(curve["star"].unique()) == {"103", "108", "114", "118", "119"}


def test_ensemble_comp_catalog_mag_uses_chart_photometry(monkeypatch):
    """Each ensemble comparison star's catalog magnitude (and its catalog error, used
    the same way KREFERR is for check stars) comes from the chart API, keyed by star
    label + filter -- not from the AAVSO report's NOTES field, which only ever carries
    one aggregate CREFMAG for the whole ensemble."""
    from photometry_workbench.parsing.chart_photometry import ChartMag

    fake_chart = {
        "103": {"V": ChartMag(10.334, 0.047), "B": ChartMag(11.364, 0.09)},
        "108": {"V": ChartMag(10.819, 0.047)},
        # "114", "118", "119" deliberately absent -- simulates a partial/failed match.
    }
    monkeypatch.setattr(
        "photometry_workbench.models.session.fetch_chart_photometry", lambda chart_id: fake_chart
    )

    report = parse_aavso_report(FIXTURES / "AAVSOReport_YZ-Boo_Ensemble_Synthetic.txt")
    general = parse_general_report(FIXTURES / "YZ+Boo_General.txt")
    session = Session(report, general)

    comp_rows = {r.star_label: r for r in session.build_summary_rows() if r.section == "Comparison stars"}
    assert comp_rows["103"].catalog_mag == pytest.approx(10.334)
    assert comp_rows["103"].kreferr == pytest.approx(0.047)
    assert comp_rows["108"].catalog_mag == pytest.approx(10.819)
    assert comp_rows["114"].catalog_mag is None  # not in the chart response -- no crash
    assert comp_rows["114"].kreferr is None
