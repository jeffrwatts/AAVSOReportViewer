import re

import numpy as np
import pytest

from photometry_workbench.parsing.aavso_report import parse_aavso_report, report_from_frame
from photometry_workbench.transform.coefficients import parse_report_header, parse_tg_ini
from photometry_workbench.transform.engine import (
    TransformError,
    _needs,
    transform_report,
    transform_set,
)

from test_parsing import FIXTURES

SESSIONS = [
    "AAVSOReport_SN-2026aaiv_BVR_20261011",
    "AAVSOReport_CY-Aqr_VBR_20260823",
    "AAVSOReport_YZ-Boo_VRB_20260814",
]


def _ta_pairing(path) -> list[dict[str, float]]:
    """For each data row of a TA report, the frame time TA used per filter -- from
    the '# BVR, AAVSO recommended using: B @ jd, V @ jd, R @ jd' line after it."""
    pairing = []
    for line in path.read_text().splitlines():
        if m := re.search(r"using: (.*)$", line):
            pairing.append({f: float(t) for f, t in re.findall(r"(\w) @ ([\d.]+)", m[1])})
    return pairing


@pytest.mark.parametrize("name", SESSIONS)
def test_transform_matches_transform_applier(name):
    """Golden parity test against real TransformApplier 2.71 output, using the very
    coefficients TA echoed into its header. Rows where TA paired a frame with a
    different cycle's partner (see _ta_pairing) are covered separately below."""
    ta_path = FIXTURES / f"{name}_TA.txt"
    raw = parse_aavso_report(FIXTURES / f"{name}.txt")
    ta = parse_aavso_report(ta_path).df
    result = transform_report(raw, parse_report_header(ta_path))
    ours = result.report.df

    assert result.skipped == []
    assert result.transformed == len(raw.df) == len(ta)
    rules = transform_set(raw.filters)[1]
    pairing = _ta_pairing(ta_path)
    same_time = np.array(
        [all(p[f] == round(d, 6) for f in _needs(filt, rules)) for p, d, filt in zip(pairing, ours["DATE"], ours["FILT"])]
    )
    assert same_time.sum() > 0.6 * len(ours)

    def diff(column):
        return (ours[column] - ta[column]).abs()[same_time]

    assert diff("MAG").max() <= 0.001 + 1e-9
    assert diff("KMAGTRAN").max() <= 0.001 + 1e-9
    # TA's MERR comes from the same formula; a few frames still land 0.001 apart,
    # presumably from TA working with less-rounded inputs than the report carries.
    assert diff("MERR").max() <= 0.001 + 1e-9
    assert (diff("MERR") < 1e-9).mean() >= 0.75
    assert (ours["TRANS"] == "YES").all()


def test_yz_boo_ta_pairing_quirk_only_affects_r():
    """YZ Boo is missing one R frame early in the night. TA pairs the k-th frame of
    each filter, so every later R frame gets the previous cycle's B/V; this engine
    pairs by nearest time instead. B and V never need R, so only R can differ."""
    ta_path = FIXTURES / "AAVSOReport_YZ-Boo_VRB_20260814_TA.txt"
    ours = transform_report(
        parse_aavso_report(FIXTURES / "AAVSOReport_YZ-Boo_VRB_20260814.txt"), parse_report_header(ta_path)
    ).report.df
    ta = parse_aavso_report(ta_path).df
    differs = (ours["MAG"] - ta["MAG"]).abs() > 0.001 + 1e-9
    assert differs.any()
    assert set(ours.loc[differs, "FILT"]) == {"R"}


def test_tg_ini_matches_ta_header():
    ini = parse_tg_ini(FIXTURES / "SA114-VIS.ini")
    header = parse_report_header(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011_TA.txt")
    assert ini.values == pytest.approx(header.values, abs=5e-4)
    assert ini.errors == pytest.approx(header.errors, abs=5e-4)
    assert "C8 VIS" in ini.description
    assert ini.source == "SA114-VIS.ini"


def _sn_transformed(**kwargs):
    raw = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    return transform_report(raw, parse_tg_ini(FIXTURES / "SA114-VIS.ini"), **kwargs)


def test_transform_output_fields():
    result = _sn_transformed(auids={"130": "000-BQG-059", "121": "000-BQG-057"})
    df = result.report.df
    assert result.set_name == "BVR"
    assert result.report.is_transformed
    # One GROUP per B/V/R cycle.
    assert df.groupby("GROUP")["FILT"].apply(lambda s: "".join(sorted(s))).eq("BRV").all()
    assert df["GROUP"].nunique() == 5
    assert set(df["CNAME"]) == {"000-BQG-059"} and set(df["KNAME"]) == {"000-BQG-057"}
    first_b = df[df["FILT"] == "B"].iloc[0]
    for key in ("VMAGSTD=14.336", "VERR=0.020", "KMAGTRAN=", "Tb_bv=0.3360", "Tb_bvErr=0.0100", "CREFMAG=13.928"):
        assert key in first_b["NOTES"]
    assert any("Tbv= 1.5830" in c for c in result.report.comments)


def test_transform_rejects_unsupported_reports():
    ta = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011_TA.txt")
    coeffs = parse_tg_ini(FIXTURES / "SA114-VIS.ini")
    with pytest.raises(TransformError, match="already transformed"):
        transform_report(ta, coeffs)

    raw = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    v_only = report_from_frame(raw.df[raw.df["FILT"] == "V"])
    with pytest.raises(TransformError, match="No transform set"):
        transform_report(v_only, coeffs)



def test_frame_without_partner_is_left_untransformed():
    raw = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    df = raw.df.copy()
    lone_r = df.index[df["FILT"] == "R"][-1]
    df.loc[lone_r, "DATE"] += 0.05  # more than an hour away from any B/V frame
    result = transform_report(report_from_frame(df), parse_tg_ini(FIXTURES / "SA114-VIS.ini"))
    assert result.transformed == len(df) - 1
    assert len(result.skipped) == 1 and "no B/V frame" in result.skipped[0]
    assert result.report.df.loc[lone_r, "TRANS"] == "NO"


def test_coefficient_sensitivity_sn_2026aaiv():
    """Coefficient-sensitivity check: move each coefficient by its error. For SN 2026aaiv
    with the VIS coefficients it's ~0.003-0.004 per filter, well under the target's
    scatter, and each filter is driven by its own color coefficient."""
    from photometry_workbench.transform.engine import coefficient_sensitivity

    raw = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    result = coefficient_sensitivity(raw, parse_tg_ini(FIXTURES / "SA114-VIS.ini"))

    assert set(result) == {"B", "V", "R"}
    assert result["B"].total == pytest.approx(0.0034, abs=0.0002)
    assert result["V"].total == pytest.approx(0.0036, abs=0.0002)
    assert result["R"].total == pytest.approx(0.0037, abs=0.0002)
    assert max(result["B"].terms, key=result["B"].terms.get) == "Tb_bv"
    assert max(result["V"].terms, key=result["V"].terms.get) == "Tv_bv"
    assert result["B"].terms["Tv_bv"] == 0  # B doesn't use V's coefficient
    for s in result.values():
        assert s.total == pytest.approx(np.sqrt(sum(t**2 for t in s.terms.values())))


@pytest.mark.parametrize("name", SESSIONS[1:])
def test_session_coefficient_sensitivity_from_ta_header(name):
    """A loaded TA report supplies its coefficients through its header. For the bright
    light curves (CY Aqr, YZ Boo) the systematic exceeds the SNR-based MERR."""
    from photometry_workbench.models.session import Session

    session = Session(parse_aavso_report(FIXTURES / f"{name}.txt"))
    with pytest.raises(ValueError):
        session.coefficient_sensitivity()
    session.attach_after_report(parse_aavso_report(FIXTURES / f"{name}_TA.txt"))
    result = session.coefficient_sensitivity()
    after = session.after.df
    merr = after.groupby("FILT")["MERR"].median()

    assert set(result) == {"B", "V", "R"}
    for filt, s in result.items():
        assert 0.007 < s.total < 0.012
        assert s.total > merr[filt]


def test_coefficient_sensitivity_needs_errors():
    from photometry_workbench.transform.coefficients import TransformCoefficients
    from photometry_workbench.transform.engine import coefficient_sensitivity

    raw = parse_aavso_report(FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt")
    coeffs = parse_tg_ini(FIXTURES / "SA114-VIS.ini")
    assert coefficient_sensitivity(raw, TransformCoefficients(coeffs.values)) == {}
