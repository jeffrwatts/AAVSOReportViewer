import pytest
from unittest.mock import MagicMock, patch

from aavso_viewer.parsing.chart_photometry import fetch_chart_auids, fetch_chart_photometry

# A trimmed real response from https://apps.aavso.org/vsp/api/chart/X42923UD/?format=json
SAMPLE_RESPONSE = {
    "chartid": "X42923UD",
    "photometry": [
        {
            "auid": "000-BKZ-381",
            "label": "103",
            "bands": [
                {"band": "V", "mag": 10.334, "error": 0.047},
                {"band": "B", "mag": 11.364, "error": 0.09},
            ],
        },
        {
            "auid": "000-BKZ-357",
            "label": "108",
            "bands": [
                {"band": "V", "mag": 10.819, "error": 0.047},
                {"band": "B", "mag": 11.518, "error": 0.099},
                {"band": "Rc", "mag": 10.397, "error": 0.057},
                {"band": "Ic", "mag": 10.002, "error": 0.065},
            ],
        },
    ],
}


def test_fetch_chart_photometry_parses_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = SAMPLE_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("aavso_viewer.parsing.chart_photometry.requests.get", return_value=mock_resp) as mock_get:
        result = fetch_chart_photometry("X42923UD")

    mock_get.assert_called_once()
    assert result["103"]["V"].mag == 10.334
    assert result["103"]["V"].error == 0.047
    assert result["103"]["B"].mag == 11.364
    # "Rc"/"Ic" (Cousins bands) map back onto the AAVSO report's plain "R"/"I" codes.
    assert set(result["108"].keys()) == {"V", "B", "R", "I"}
    assert result["108"]["R"].mag == 10.397
    assert result["108"]["I"].mag == 10.002


def test_fetch_chart_auids_maps_labels():
    mock_resp = MagicMock()
    mock_resp.json.return_value = SAMPLE_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("aavso_viewer.parsing.chart_photometry.requests.get", return_value=mock_resp):
        assert fetch_chart_auids("X42923UD") == {"103": "000-BKZ-381", "108": "000-BKZ-357"}


# Trimmed from X43295PS (CzeV616), whose chart repeats labels 105 and 119. VPhot's
# report names the second of each '105_1' / '119_1' (V 10.549 and 11.886 -- the
# ensemble's CREFMAG 11.258 is the mean of 10.549, 11.340 and 11.886).
DUPLICATE_LABELS_RESPONSE = {
    "photometry": [
        {"auid": "000-BKK-881", "label": "105", "bands": [{"band": "V", "mag": 10.47, "error": 0.026}]},
        {"auid": "000-BQM-378", "label": "105", "bands": [{"band": "V", "mag": 10.549, "error": 0.036}]},
        {"auid": "000-BQM-380", "label": "113", "bands": [{"band": "V", "mag": 11.34, "error": 0.006}]},
        {"auid": "000-BLP-292", "label": "119", "bands": [{"band": "V", "mag": 11.851, "error": 0.014}]},
        {"auid": "000-BQM-381", "label": "119", "bands": [{"band": "V", "mag": 11.886, "error": 0.006}]},
    ]
}


def test_duplicate_chart_labels_named_like_vphot():
    mock_resp = MagicMock()
    mock_resp.json.return_value = DUPLICATE_LABELS_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("aavso_viewer.parsing.chart_photometry.requests.get", return_value=mock_resp):
        mags = fetch_chart_photometry("X43295PS")
        auids = fetch_chart_auids("X43295PS")

    assert set(mags) == {"105", "105_1", "113", "119", "119_1"}
    assert mags["105"]["V"].mag == 10.47
    assert mags["105_1"]["V"].mag == 10.549
    assert mags["119_1"]["V"].mag == 11.886
    assert (mags["105_1"]["V"].mag + mags["113"]["V"].mag + mags["119_1"]["V"].mag) / 3 == pytest.approx(11.258, abs=0.0005)
    assert auids["105_1"] == "000-BQM-378"
    assert auids["119"] == "000-BLP-292"
