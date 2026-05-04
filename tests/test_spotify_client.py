import time
from unittest.mock import MagicMock, patch

import pytest

import spotify_client
from spotify_client import (
    _get_access_token,
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

# A valid cached token that won't expire for a long time
_VALID_CACHE = {"token": "fake_token", "expires_at": time.time() + 7200}


class TestGetAccessToken:
    def test_uses_cached_token_when_valid(self):
        cache = {"token": "cached", "expires_at": time.time() + 3600}
        with patch.dict("spotify_client._token_cache", cache, clear=True):
            token = _get_access_token()
        assert token == "cached"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("spotify_client.requests.post")
    def test_fetches_new_token_when_expired(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "new_token", "expires_in": 3600}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        # Cache has expired token
        expired_cache = {"token": "old_token", "expires_at": time.time() - 100}
        with patch.dict("spotify_client._token_cache", expired_cache, clear=True):
            token = _get_access_token()
        assert token == "new_token"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("spotify_client.requests.post")
    def test_fetches_token_when_cache_empty(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "fresh", "expires_in": 3600}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch.dict("spotify_client._token_cache", {}, clear=True):
            token = _get_access_token()
        assert token == "fresh"


class TestVerifySpotifyLink:
    @patch("spotify_client.requests.get")
    def test_matching_track(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "Jack (Hoax Rework)"}
        mock_get.return_value = mock_response

        with patch.dict("spotify_client._token_cache", _VALID_CACHE, clear=True):
            result = verify_spotify_link(
                "Jack (Hoax Rework)",
                "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
            )
        assert result == (1.0, "Jack (Hoax Rework)")

    @patch("spotify_client.requests.get")
    def test_mismatched_track(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "Totally Wrong Track"}
        mock_get.return_value = mock_response

        with patch.dict("spotify_client._token_cache", _VALID_CACHE, clear=True):
            score, title = verify_spotify_link(
                "Jack (Hoax Rework)",
                "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
            )
        assert score < 0.5
        assert title == "Totally Wrong Track"

    @patch("spotify_client.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        with patch.dict("spotify_client._token_cache", _VALID_CACHE, clear=True):
            score = verify_spotify_link(
                "Jack",
                "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
            )
        assert score is None

    @patch.dict("os.environ", FAKE_ENV)
    @patch("spotify_client.requests.post")
    @patch("spotify_client.requests.get")
    def test_retries_on_401_with_fresh_token(self, mock_get, mock_post):
        """A 401 should clear the cache, fetch a new token, and retry once."""
        stale_cache = {"token": "stale_token", "expires_at": time.time() + 3600}

        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "new_token", "expires_in": 3600}
        token_resp.raise_for_status = MagicMock()
        mock_post.return_value = token_resp

        resp_401 = MagicMock(status_code=401)
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {"name": "Good Track"}
        mock_get.side_effect = [resp_401, resp_200]

        with patch.dict("spotify_client._token_cache", stale_cache, clear=True):
            result = verify_spotify_link(
                "Good Track",
                "https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld",
            )
        assert result == (1.0, "Good Track")
        assert mock_post.call_count == 1  # fetched a new token
        assert mock_get.call_count == 2   # two attempts

    def test_non_spotify_url_returns_none(self):
        score = verify_spotify_link(
            "Jack",
            "https://www.beatport.com/release/jack/5878031",
        )
        assert score is None
