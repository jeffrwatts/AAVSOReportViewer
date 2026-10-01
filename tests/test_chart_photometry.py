from unittest.mock import MagicMock, patch

from aavso_viewer.parsing.chart_photometry import fetch_chart_photometry

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
