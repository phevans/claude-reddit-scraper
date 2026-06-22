from unittest.mock import MagicMock, patch

from beatport_client import verify_beatport_link


class TestVerifyBeatportLinkRelease:
    @patch("beatport_client.get_release")
    def test_matching_release(self, mock_get_release):
        mock_get_release.return_value = {"name": "Home Alone", "track_count": 2}
        result = verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/5898264",
        )
        assert result[0] == 1.0
        assert result[1] == "Home Alone"
        assert result[2] == 2

    @patch("beatport_client.get_release")
    def test_partial_match(self, mock_get_release):
        mock_get_release.return_value = {"name": "Home Alone EP", "track_count": 4}
        score, title, count = verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/5898264",
        )
        assert 0.5 < score < 1.0
        assert title == "Home Alone EP"
        assert count == 4

    @patch("beatport_client.get_release_tracks")
    @patch("beatport_client.get_release")
    def test_falls_back_to_track_count(self, mock_get_release, mock_tracks):
        mock_get_release.return_value = {"name": "Home Alone"}
        mock_tracks.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        _, _, count = verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/5898264",
        )
        assert count == 3

    @patch("beatport_client.get_release", return_value=None)
    def test_api_failure_returns_none(self, mock_get_release):
        assert verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/5898264",
        ) is None

    def test_non_beatport_url_returns_none(self):
        assert verify_beatport_link(
            "Home Alone",
            "https://open.spotify.com/track/abc123",
        ) is None


class TestVerifyBeatportLinkTrack:
    @patch("beatport_client.get_track")
    def test_track_url_with_original_mix(self, mock_get_track):
        mock_get_track.return_value = {"name": "Gritty", "mix_name": "Original Mix"}
        score, title, count = verify_beatport_link(
            "Gritty",
            "https://www.beatport.com/track/gritty/12345",
        )
        assert score == 1.0
        assert title == "Gritty"
        assert count == 1

    @patch("beatport_client.get_track")
    def test_track_url_with_remix(self, mock_get_track):
        mock_get_track.return_value = {"name": "Gritty", "mix_name": "VIP Mix"}
        _, title, count = verify_beatport_link(
            "Gritty",
            "https://www.beatport.com/track/gritty/12345",
        )
        assert title == "Gritty (VIP Mix)"
        assert count == 1

    @patch("beatport_client.get_track", return_value=None)
    def test_track_api_failure(self, mock_get_track):
        assert verify_beatport_link(
            "Gritty",
            "https://www.beatport.com/track/gritty/12345",
        ) is None


class TestVerifyBeatportLinkBadUrl:
    """End-to-end: a wrong/deleted Beatport URL (404 from the API) must
    return None, not raise. Regression for the crash where one bad link
    aborted the whole scrape."""

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_release_404_returns_none(self, mock_request, mock_token):
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 404
        resp.reason = "Not Found"
        resp.url = "https://api.beatport.com/v4/catalog/releases/1/"
        resp.text = '{"detail": "Not found."}'
        mock_request.return_value = resp

        assert verify_beatport_link(
            "Home Alone",
            "https://www.beatport.com/release/home-alone/99999999",
        ) is None
