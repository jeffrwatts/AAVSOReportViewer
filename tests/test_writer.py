import pandas as pd
import pytest

from aavso_viewer.parsing.aavso_report import STANDARD_COLUMNS, parse_aavso_report
from aavso_viewer.parsing.aavso_writer import format_report, write_aavso_report
from aavso_viewer.transform.coefficients import parse_tg_ini
from aavso_viewer.transform.engine import transform_report

from test_parsing import FIXTURES

REPORTS = [
    "AAVSOReport_CY-Aqr_VBR_20260823.txt",
    "AAVSOReport_CY-Aqr_VBR_20260823_TA.txt",
    "AAVSOReport_SN-2026aaiv_BVR_20261011.txt",
    "AAVSOReport_YZ-Boo_VRB_20260814.txt",
    "AAVSOReport_YZ-Boo_Ensemble_Synthetic.txt",
]


def _assert_same_rows(a: pd.DataFrame, b: pd.DataFrame):
    assert len(a) == len(b)
    for column in STANDARD_COLUMNS:
        left, right = a[column].reset_index(drop=True), b[column].reset_index(drop=True)
        if pd.api.types.is_numeric_dtype(left):
            pd.testing.assert_series_equal(left, right, check_names=False, atol=1e-9)
        else:
            assert left.astype(str).tolist() == right.astype(str).tolist(), column


@pytest.mark.parametrize("name", REPORTS)
def test_round_trip(name, tmp_path):
    report = parse_aavso_report(FIXTURES / name)
    out = tmp_path / name
    write_aavso_report(report, out)
    again = parse_aavso_report(out)
    _assert_same_rows(report.df, again.df)
    assert again.header == report.header


def test_header_parameters_preserved():
    report = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    assert report.header == {
        "TYPE": "EXTENDED", "OBSCODE": "WJRA", "SOFTWARE": "VPhot 4.0.83",
        "DELIM": ",", "DATE": "JD", "OBSTYPE": "CCD",
    }
    assert format_report(report).splitlines()[0:2] == ["#TYPE=EXTENDED", "#OBSCODE=WJRA"]


def test_transformed_report_round_trips_with_comments(tmp_path):
    raw = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    transformed = transform_report(raw, parse_tg_ini(FIXTURES / "SA114-VIS.ini")).report
    out = tmp_path / "ta.txt"
    write_aavso_report(transformed, out)
    text = out.read_text()
    # The original untransformed row is kept as a comment, TA-style.
    assert "#SN 2026aaiv,2461324.824745,14.336,0.020,B,NO" in text
    assert "#Tbv= 1.5830, +/- 0.0170" in text
    again = parse_aavso_report(out)
    _assert_same_rows(transformed.df, again.df)
    assert again.is_transformed


def test_writer_rejects_invalid_fields(tmp_path):
    report = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    report.df.loc[0, "NOTES"] = "|comment=one,two"
    with pytest.raises(ValueError, match="NOTES contains the delimiter"):
        write_aavso_report(report, tmp_path / "bad.txt")
    assert not (tmp_path / "bad.txt").exists()

    report = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    report.df.loc[0, "MERR"] = 1234.5
    with pytest.raises(ValueError, match="MERR is longer than 6"):
        format_report(report)
