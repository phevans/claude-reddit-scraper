from unittest.mock import patch, MagicMock

from beatport_client import _fetch_beatport_title, verify_beatport_link


BEATPORT_HTML = """
<html><head>
<meta property="og:title" content="Acelin, Maddy Lucas - Home Alone (Extended Mix) [YosH] | Music &amp; Downloads on Beatport"/>
</head></html>
"""

BEATPORT_HTML_NO_META = """
<html><head><title>Beatport</title></head></html>
"""


class TestFetchBeatportTitle:
    @patch("beatport_client.requests.get")
    def test_extracts_title_from_og_tag(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = BEATPORT_HTML
        mock_get.return_value = mock_response

        title = _fetch_beatport_title("https://www.beatport.com/release/home-alone/5898264")
        assert title == "Home Alone (Extended Mix)"

    @patch("beatport_client.requests.get")
    def test_returns_none_on_404(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        assert _fetch_beatport_title("https://www.beatport.com/release/x/123") is None

    @patch("beatport_client.requests.get")
    def test_returns_none_when_no_og_title(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = BEATPORT_HTML_NO_META
        mock_get.return_value = mock_response

        assert _fetch_beatport_title("https://www.beatport.com/release/x/123") is None


class TestVerifyBeatportLink:
    @patch("beatport_client.requests.get")
    def test_matching_release(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = BEATPORT_HTML
        mock_get.return_value = mock_response

        result = verify_beatport_link(
            "Home Alone (Extended Mix)",
            "https://www.beatport.com/release/home-alone/5898264",
        )
        assert result[0] == 1.0
        assert result[1] == "Home Alone (Extended Mix)"

    @patch("beatport_client.requests.get")
    def test_partial_match(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = BEATPORT_HTML
        mock_get.return_value = mock_response

        score, title, _ = verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/5898264",
        )
        assert 0.5 < score < 1.0
        assert title == "Home Alone (Extended Mix)"

    def test_non_beatport_url_returns_none(self):
        result = verify_beatport_link(
            "Home Alone",
            "https://open.spotify.com/track/abc123",
        )
        assert result is None

    @patch("beatport_client.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.return_value = mock_response

        result = verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/5898264",
        )
        assert result is None
