import pytest

from aavso_viewer.parsing.aavso_report import parse_aavso_report, report_from_frame
from aavso_viewer.parsing.aavso_writer import write_aavso_report
from aavso_viewer.submission.builder import (
    ErrorMethod,
    SubmissionError,
    build_submission,
    choose_method,
)
from aavso_viewer.transform.coefficients import parse_tg_ini
from aavso_viewer.transform.engine import transform_report

from test_parsing import FIXTURES

SN = "AAVSOReport_SN-2026aaiv_BVR_20261011"


@pytest.mark.parametrize(
    "combine, varies, ensemble, expected",
    [
        (True, False, False, ErrorMethod.STD_TARGET),
        (True, False, True, ErrorMethod.STD_TARGET),
        (True, True, False, ErrorMethod.STD_CHECK),
        (True, True, True, ErrorMethod.STD_CHECK),
        (False, None, False, ErrorMethod.SNR),
        (False, None, True, ErrorMethod.COMP_SPREAD),
    ],
)
def test_choose_method(combine, varies, ensemble, expected):
    assert choose_method(combine, varies, ensemble) == expected


def test_choose_method_needs_variability_answer_when_combining():
    with pytest.raises(ValueError):
        choose_method(True, None, False)


def _sn_ta():
    return parse_aavso_report(FIXTURES / f"{SN}_TA.txt")


def test_sn_2026aaiv_combined_target_std():
    """Golden: 5 TA-transformed frames per filter → one point per filter, MERR = the
    target's own Std (a supernova doesn't vary over the 4.6 minutes)."""
    sub = build_submission(_sn_ta(), ErrorMethod.STD_TARGET)
    rows = sub.report.df.set_index("FILT")
    assert list(rows.index) == ["B", "V", "R"]
    assert rows.loc["B", "MAG"] == pytest.approx(14.472) and rows.loc["B", "MERR"] == pytest.approx(0.056)
    assert rows.loc["V", "MAG"] == pytest.approx(13.267) and rows.loc["V", "MERR"] == pytest.approx(0.016)
    assert rows.loc["R", "MAG"] == pytest.approx(12.704) and rows.loc["R", "MERR"] == pytest.approx(0.037)
    assert set(rows["GROUP"]) == {"0001"}
    assert (rows["TRANS"] == "YES").all()
    assert rows.loc["B", "DATE"] == pytest.approx(2461324.826347, abs=1e-6)  # mean of the 5 frame times
    notes = rows.loc["B", "NOTES"]
    assert "NCOMB=5" in notes and "MERRMETHOD=STD_TARGET" in notes
    assert "CREFMAG=13.928" in notes  # constant keys carried over
    assert sub.warnings == []
    assert sub.report.header["SOFTWARE"].startswith("VPhot 4.0.83 + AAVSO Report Viewer")


def test_sn_2026aaiv_combined_check_std():
    sub = build_submission(_sn_ta(), ErrorMethod.STD_CHECK)
    merr = sub.report.df.set_index("FILT")["MERR"]
    assert merr.to_dict() == pytest.approx({"B": 0.027, "V": 0.011, "R": 0.027})
    assert sub.warnings == []  # check star 121 is within 2 mag of the SN in every filter


def test_in_app_transform_gives_same_submission_as_ta():
    raw = parse_aavso_report(FIXTURES / f"{SN}.txt")
    ours = transform_report(raw, parse_tg_ini(FIXTURES / "SA114-VIS.ini")).report
    a = build_submission(ours, ErrorMethod.STD_TARGET).report.df.set_index("FILT")
    b = build_submission(_sn_ta(), ErrorMethod.STD_TARGET).report.df.set_index("FILT")
    assert (a["MAG"] - b["MAG"]).abs().max() <= 0.001
    assert (a["MERR"] - b["MERR"]).abs().max() <= 0.001


def test_check_star_too_far_warns():
    report = _sn_ta()
    report.df["KEFF"] = report.df["MAG"] - 2.5
    sub = build_submission(report, ErrorMethod.STD_CHECK)
    assert len(sub.warnings) == 3 and "2.50 mag" in sub.warnings[0]


def test_combining_needs_two_frames():
    df = _sn_ta().df
    with pytest.raises(SubmissionError, match="at least 2"):
        build_submission(report_from_frame(df.iloc[:3]), ErrorMethod.STD_TARGET)


def test_snr_path_keeps_frames_and_round_trips(tmp_path):
    ta = _sn_ta()
    sub = build_submission(ta, ErrorMethod.SNR)
    assert len(sub.report.df) == len(ta.df)
    assert (sub.report.df["MERR"].values == ta.df["MERR"].values).all()
    assert sub.report.df["NOTES"].str.contains("MERRMETHOD=SNR").all()
    out = tmp_path / "submit.txt"
    write_aavso_report(sub.report, out)
    again = parse_aavso_report(out)
    assert len(again.df) == len(ta.df) and again.is_transformed


def test_comp_spread_not_supported_yet():
    with pytest.raises(SubmissionError, match="aren't supported yet"):
        build_submission(_sn_ta(), ErrorMethod.COMP_SPREAD)
