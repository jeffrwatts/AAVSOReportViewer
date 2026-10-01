from aavso_viewer.models.session import Session
from aavso_viewer.parsing.aavso_report import parse_aavso_report

from test_parsing import FIXTURES


def test_missing_amass_column_does_not_crash():
    """AMASS is optional in the AAVSO Extended Format -- a report that omits it
    entirely must not crash build_summary_rows() or airmass_series()."""
    report = parse_aavso_report(FIXTURES / "AAVSOReport_CY-Aqr_VBR_20260823_no_amass.txt")
    assert "AMASS" not in report.df.columns
    session = Session(report)

    rows = session.build_summary_rows()
    for r in rows:
        if r.section == "Check stars":
            assert r.trend is None

    airmass_df = session.airmass_series()
    assert airmass_df.empty
    assert list(airmass_df.columns) == ["DATE", "FILT", "value"]
