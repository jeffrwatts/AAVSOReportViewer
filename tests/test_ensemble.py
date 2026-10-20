"""Ensemble transform and ensemble light-curve MERR, against a real B/V/R ensemble
session (SN 2026aaiv, comps 130 + 135, check 121) and the real chart catalog for
those stars (fetched from apps.aavso.org on 2026-10-11)."""

import json

import numpy as np
import pytest

from aavso_viewer.models.session import Session
from aavso_viewer.parsing.aavso_report import parse_aavso_report
from aavso_viewer.parsing.aavso_writer import write_aavso_report
from aavso_viewer.parsing.chart_photometry import _BAND_TO_FILTER, ChartMag
from aavso_viewer.parsing.general_report import parse_general_report
from aavso_viewer.submission.builder import ErrorMethod, SubmissionError, build_submission, frame_consistency
from aavso_viewer.transform.coefficients import parse_tg_ini
from aavso_viewer.transform.engine import TransformError, comp_estimates, per_comp_estimates, transform_report

from test_parsing import FIXTURES

ENSEMBLE = FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011_Ensemble.txt"
GENERAL = FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011_Ensemble_General.txt"
COMPS = ["130", "135"]


def _catalog():
    data = json.loads((FIXTURES / "chart_X43237EU.json").read_text())
    return {
        s["label"]: {_BAND_TO_FILTER.get(b["band"], b["band"]): ChartMag(b["mag"], b["error"]) for b in s["bands"]}
        for s in data["photometry"]
    }


def _coeffs():
    return parse_tg_ini(FIXTURES / "SA114-VIS.ini")


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr("aavso_viewer.models.session.fetch_chart_photometry", lambda chart_id: _catalog())
    monkeypatch.setattr(
        "aavso_viewer.models.session.fetch_chart_auids", lambda chart_id: {"121": "000-BQG-057"}
    )
    return Session(parse_aavso_report(ENSEMBLE), parse_general_report(GENERAL))


def test_untransformed_comp_estimates_reproduce_vphot_ensemble():
    """VPhot's ensemble magnitude is the plain average of the per-comp estimates --
    confirms the General Report join and the catalog both match VPhot's ensemble."""
    report = parse_aavso_report(ENSEMBLE)
    est = comp_estimates(report, parse_general_report(GENERAL).df, COMPS, _catalog())
    assert (est["n"] == 2).all()
    assert (est["mean"] - report.df["MAG"]).abs().max() <= 0.001


SINGLE_COMP_130 = FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011.txt"
SINGLE_COMP_130_TA = FIXTURES / "AAVSOReport_SN-2026aaiv_BVR_20261011_TA.txt"


@pytest.mark.parametrize("transformed", [False, True])
def test_per_comp_estimate_matches_single_comp_report(transformed):
    """The target estimated against comp 130 alone, from the ensemble's General
    Report, equals VPhot's single-comp report against 130 for the same frames (and
    TA's transform of it). VPhot's comp columns are leave-one-out -- each comp
    measured against the other comps -- and reading them as measured against the whole
    ensemble put each 2-comp estimate off by half the comps' disagreement (up to
    0.04 here), doubling the spread."""
    report = parse_aavso_report(ENSEMBLE)
    coeffs = _coeffs() if transformed else None
    per_comp = per_comp_estimates(report, parse_general_report(GENERAL).df, COMPS, _catalog(), coeffs)
    single = parse_aavso_report(SINGLE_COMP_130_TA if transformed else SINGLE_COMP_130).df
    merged = report.df[["DATE", "FILT"]].join(per_comp["130"]).merge(single[["DATE", "FILT", "MAG"]], on=["DATE", "FILT"])
    assert len(merged) == 15
    assert (merged["130"] - merged["MAG"]).abs().max() <= 0.002


def test_average_comp_transform_equals_per_comp_transform():
    """Transforming against the ensemble's average comp (all the AAVSO report
    carries) equals transforming against each comp and averaging -- the reason the
    ensemble transform needs no General Report."""
    report = parse_aavso_report(ENSEMBLE)
    transformed = transform_report(report, _coeffs()).report.df
    per_comp = comp_estimates(report, parse_general_report(GENERAL).df, COMPS, _catalog(), _coeffs())
    assert (per_comp["mean"] - transformed["MAG"]).abs().max() <= 0.001


def test_ensemble_transform_output(session):
    result = session.apply_transform(_coeffs())
    df = result.report.df
    assert result.skipped == [] and result.report.is_transformed and result.report.is_ensemble
    assert (df["CNAME"] == "ENSEMBLE").all() and df["CMAG"].isna().all()
    assert (df["KNAME"] == "000-BQG-057").all()
    # An ensemble report's KMAG is the check star's calculated (here transformed) magnitude.
    assert np.allclose(df["KMAG"], df["KMAGTRAN"])
    assert df["NOTES"].str.contains("ENSCOMPS=130;135").all()
    # The transform moves the check star much closer to catalog in B (+0.119 -> +0.004).
    b = df["FILT"] == "B"
    assert abs(df.loc[b, "KMAGTRAN"].mean() - 12.738) < abs(session.aavso.df.loc[b, "KMAGSTD"].mean() - 12.738)


def test_ensemble_transform_without_general_report(monkeypatch):
    monkeypatch.setattr("aavso_viewer.models.session.fetch_chart_auids", lambda chart_id: {})
    session = Session(parse_aavso_report(ENSEMBLE))
    df = session.apply_transform(_coeffs()).report.df
    assert (df["TRANS"] == "YES").all()
    assert not df["NOTES"].str.contains("ENSCOMPS").any()
    with pytest.raises(ValueError, match="General Report"):
        session.comp_spread(transformed=True)


def test_partial_ensemble_membership_is_refused(session):
    general = session.general.df
    general.loc[general["Filter"] == "R", "135"] = np.nan
    with pytest.raises(TransformError, match="135 has no R"):
        session.apply_transform(_coeffs())


def test_comp_spread_submission(session, tmp_path):
    """MERR is the plain Std across the comps (Technique #3, as the mentor confirmed --
    not Std/sqrt(N)) combined with the SNR uncertainty."""
    session.apply_transform(_coeffs())
    spread = session.comp_spread(transformed=True)
    sub = build_submission(session.after, ErrorMethod.COMP_SPREAD, comp_spread=spread)
    df = sub.report.df
    assert len(df) == len(session.after.df)
    expected = np.hypot(spread["std"], session.after.df["MERR"]).round(3)
    assert np.allclose(df["MERR"], expected)
    assert (df["MERR"] >= session.after.df["MERR"]).all()
    assert df["NOTES"].str.contains(f"MERRMETHOD=COMP_SPREAD").all()
    assert not df["NOTES"].str.contains("COMPSPREAD").any()
    assert df["NOTES"].str.contains("NCOMP=2").all()
    assert sub.warnings == []
    write_aavso_report(sub.report, tmp_path / "submit.txt")
    assert parse_aavso_report(tmp_path / "submit.txt").is_ensemble


def test_comp_spread_needs_two_comps(session):
    spread = session.comp_spread(transformed=False)
    spread["n"] = 1
    with pytest.raises(SubmissionError, match="fewer than 2"):
        build_submission(session.aavso, ErrorMethod.COMP_SPREAD, comp_spread=spread)


def test_comp_spread_flags_mismatched_catalog(session, monkeypatch):
    catalog = _catalog()
    catalog["135"]["V"] = ChartMag(catalog["135"]["V"].mag + 0.1, 0.05)
    monkeypatch.setattr("aavso_viewer.models.session.fetch_chart_photometry", lambda chart_id: catalog)
    spread = Session(session.aavso, session.general).comp_spread(transformed=False)
    sub = build_submission(session.aavso, ErrorMethod.COMP_SPREAD, comp_spread=spread)
    assert sub.warnings and "may not match" in sub.warnings[0]


def test_frame_consistency_uses_comp_spread(session):
    """For an ensemble, a single frame's uncertainty is the comp spread combined with
    MERR (Technique #3) -- larger than MERR alone, so the ratio drops."""
    session.apply_transform(_coeffs())
    spread = session.comp_spread(transformed=True)
    with_spread = {r.filt: r for r in frame_consistency(session.after, spread)}
    snr_only = {r.filt: r for r in frame_consistency(session.after)}
    for filt in "BVR":
        assert with_spread[filt].across == snr_only[filt].across
        assert with_spread[filt].single > snr_only[filt].single
        assert with_spread[filt].ratio < snr_only[filt].ratio


def test_comp_membership_clean_run(session):
    """Every comp measured in every frame of every filter: n/n everywhere."""
    membership = session.comp_membership()
    assert set(membership) == set(COMPS)
    for by_filter in membership.values():
        for m in by_filter.values():
            assert m.measured == m.frames == 5
            assert m.missing == []


def test_comp_membership_reports_dropped_frame(session):
    general = session.general.df
    row = general.index[general["Filter"] == "B"][2]
    general.loc[row, "135"] = np.nan
    m = session.comp_membership()["135"]["B"]
    assert (m.measured, m.frames) == (4, 5)
    assert m.missing == [pytest.approx(general.at[row, "JD"])]
    assert session.comp_membership()["130"]["B"].measured == 5


def test_comp_offsets_match_vphot_comp_columns(session):
    """Untransformed, a comp's offset is its General Report magnitude (measured
    against the other comps, as VPhot shows it) minus its catalog magnitude. With two
    comps the offsets mirror each other."""
    offsets = session.comp_offsets(transformed=False)
    catalog = _catalog()
    general = session.general.df
    for comp in COMPS:
        for filt in "BVR":
            measured = general.loc[general["Filter"] == filt, comp].median()
            assert offsets.at[comp, filt] == pytest.approx(measured - catalog[comp][filt].mag, abs=0.002)
    assert np.allclose(offsets.sum(), 0, atol=1e-9)


def test_comp_offsets_transformed_needs_in_app_transform(session):
    with pytest.raises(ValueError, match="wasn't produced in this app"):
        session.comp_offsets(transformed=True)
    session.apply_transform(_coeffs())
    after = session.comp_offsets(transformed=True)
    assert list(after.columns) == session.filters
    assert set(after.index) == set(COMPS)
