import json
import time
from unittest.mock import MagicMock, mock_open, patch

from beatport_playlist import (
    _load_cached_token,
    _token_is_valid,
    add_tracks_to_playlist,
    extract_release_id,
    get_track_ids,
)


class TestExtractReleaseId:
    def test_standard_url(self):
        url = "https://www.beatport.com/release/home-alone/5898264"
        assert extract_release_id(url) == 5898264

    def test_url_with_query_params(self):
        url = "https://www.beatport.com/release/some-release/12345?ref=foo"
        assert extract_release_id(url) == 12345

    def test_non_release_url(self):
        url = "https://www.beatport.com/track/some-track/99999"
        assert extract_release_id(url) is None

    def test_non_beatport_url(self):
        assert extract_release_id("https://open.spotify.com/track/abc") is None

    def test_empty_string(self):
        assert extract_release_id("") is None


class TestTokenValidity:
    def test_valid_token(self):
        token_data = {"expires_at": time.time() + 3600}
        assert _token_is_valid(token_data) is True

    def test_expired_token(self):
        token_data = {"expires_at": time.time() - 100}
        assert _token_is_valid(token_data) is False

    def test_about_to_expire(self):
        token_data = {"expires_at": time.time() + 30}  # within 60s buffer
        assert _token_is_valid(token_data) is False

    def test_missing_expires_at(self):
        assert _token_is_valid({}) is False


class TestLoadCachedToken:
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_returns_none_when_no_file(self, mock_file):
        assert _load_cached_token() is None

    @patch("builtins.open", mock_open(read_data='{"access_token": "abc"}'))
    def test_returns_token_data(self):
        result = _load_cached_token()
        assert result == {"access_token": "abc"}

    @patch("builtins.open", mock_open(read_data="not json"))
    def test_returns_none_on_bad_json(self):
        assert _load_cached_token() is None


class TestGetTrackIds:
    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_returns_track_ids(self, mock_request, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": 111, "name": "Track A"},
                {"id": 222, "name": "Track B"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        ids = get_track_ids("https://www.beatport.com/release/test/5898264")
        assert ids == [111, 222]

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_handles_flat_list_response(self, mock_request, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 333}]
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        ids = get_track_ids("https://www.beatport.com/release/test/123")
        assert ids == [333]

    def test_returns_empty_for_non_release_url(self):
        assert get_track_ids("https://open.spotify.com/track/abc") == []


class TestAddTracksToPlaylist:
    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_posts_track_ids(self, mock_request, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"playlist_id": 42, "track_ids": [111, 222]}
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        result = add_tracks_to_playlist(42, [111, 222])
        # `added` is appended on top of the API response.
        assert result == {"playlist_id": 42, "track_ids": [111, 222], "added": 2}

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args
        assert call_kwargs[0][0] == "POST"
        assert "/v4/my/playlists/42/tracks/bulk/" in call_kwargs[0][1]
        assert call_kwargs[1]["json"] == {"track_ids": [111, 222]}

    @patch("beatport_playlist._BEATPORT_BULK_LIMIT", 100)
    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_batches_large_track_lists(self, mock_request, mock_token):
        # 250 tracks across a 100-cap should split into 3 requests
        # (100 + 100 + 50). The bulk endpoint isn't publicly documented
        # to have a cap, but if we ever blow through one we want to
        # have already split the payload.
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        track_ids = list(range(250))
        result = add_tracks_to_playlist(42, track_ids)

        assert mock_request.call_count == 3
        batch_sizes = [len(c.kwargs["json"]["track_ids"])
                       for c in mock_request.call_args_list]
        assert batch_sizes == [100, 100, 50]
        assert result["added"] == 250

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_empty_list_makes_no_request(self, mock_request, mock_token):
        result = add_tracks_to_playlist(42, [])
        mock_request.assert_not_called()
        assert result == {"added": 0}


