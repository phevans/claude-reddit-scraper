from unittest.mock import patch, MagicMock

import pytest

from spotify_client import (
    _parse_spotify_url,
    compute_similarity,
    verify_spotify_link,
)


class TestParseSpotifyUrl:
    def test_track_url(self):
        result = _parse_spotify_url("https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld")
        assert result == ("track", "0Uekv0MjYK8cnsWDvQ3fld")

    def test_album_url(self):
        result = _parse_spotify_url("https://open.spotify.com/album/79qR8r7Flf2NAY9DVrIJ4L")
        assert result == ("album", "79qR8r7Flf2NAY9DVrIJ4L")

    def test_invalid_url(self):
        assert _parse_spotify_url("https://www.beatport.com/release/something/123") is None

    def test_empty_url(self):
        assert _parse_spotify_url("") is None


class TestComputeSimilarity:
    def test_exact_match(self):
        assert compute_similarity("Jack (Hoax Rework)", "Jack (Hoax Rework)") == 1.0

    def test_case_insensitive(self):
        assert compute_similarity("PLANETS", "planets") == 1.0

    def test_partial_match(self):
        score = compute_similarity("Jack", "Jack (Hoax Rework)")
        assert 0.3 < score < 0.7

    def test_no_match(self):
        score = compute_similarity("Completely Different", "Nothing Similar At All")
        assert score < 0.5


FAKE_ENV = {
    "SPOTIFY_CLIENT_ID": "fake_id",
    "SPOTIFY_CLIENT_SECRET": "fake_secret",
}


class TestVerifySpotifyLink:
    @patch("spotify_client._token_cache", {"token": "fake_token"})
    @patch("spotify_client.requests.get")
    def test_matching_track(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "Jack (Hoax Rework)"}
        mock_get.return_value = mock_response

        score = verify_spotify_link(
            "Jack (Hoax Rework)",
            "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
        )
        assert score == 1.0

    @patch("spotify_client._token_cache", {"token": "fake_token"})
    @patch("spotify_client.requests.get")
    def test_mismatched_track(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "Totally Wrong Track"}
        mock_get.return_value = mock_response

        score = verify_spotify_link(
            "Jack (Hoax Rework)",
            "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
        )
        assert score < 0.5

    @patch("spotify_client._token_cache", {"token": "fake_token"})
    @patch("spotify_client.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        score = verify_spotify_link(
            "Jack",
            "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
        )
        assert score is None

    def test_non_spotify_url_returns_none(self):
        score = verify_spotify_link(
            "Jack",
            "https://www.beatport.com/release/jack/5878031",
        )
        assert score is None
