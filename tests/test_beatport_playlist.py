import json
import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from beatport_playlist import (
    _load_cached_token,
    _token_is_valid,
    add_tracks_to_playlist,
    extract_release_id,
    get_my_playlists,
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


class TestGetMyPlaylists:
    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_returns_playlist_list(self, mock_request, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": 1, "name": "My DnB"},
                {"id": 2, "name": "Favorites"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        playlists = get_my_playlists()
        assert playlists == [
            {"id": 1, "name": "My DnB"},
            {"id": 2, "name": "Favorites"},
        ]


class TestAddTracksToPlaylist:
    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_posts_track_ids(self, mock_request, mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"playlist_id": 42, "track_ids": [111, 222]}
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        result = add_tracks_to_playlist(42, [111, 222])
        assert result == {"playlist_id": 42, "track_ids": [111, 222]}

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args
        assert call_kwargs[0][0] == "POST"
        assert "/v4/my/playlists/42/tracks/bulk/" in call_kwargs[0][1]
        assert call_kwargs[1]["json"] == {"track_ids": [111, 222]}


class TestAppPlaylistRoutes:
    @pytest.fixture
    def client(self):
        from app import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    @patch("app.get_my_playlists", return_value=[{"id": 1, "name": "Test"}])
    def test_get_playlists(self, mock_playlists, client):
        resp = client.get("/beatport/playlists")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == [{"id": 1, "name": "Test"}]

    @patch("app.get_my_playlists", side_effect=RuntimeError("No credentials"))
    def test_get_playlists_error(self, mock_playlists, client):
        resp = client.get("/beatport/playlists")
        assert resp.status_code == 500
        assert "error" in resp.get_json()

    @patch("app.get_track_ids", return_value=[111, 222])
    def test_resolve_tracks(self, mock_resolve, client):
        resp = client.post(
            "/beatport/resolve-tracks",
            json={"beatport_url": "https://www.beatport.com/release/test/123"},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"track_ids": [111, 222]}

    @patch("app.add_tracks_to_playlist", return_value={"status": "ok"})
    def test_add_tracks(self, mock_add, client):
        resp = client.post(
            "/beatport/add-tracks",
            json={"playlist_id": 42, "track_ids": [111, 222]},
        )
        assert resp.status_code == 200
        mock_add.assert_called_once_with(42, [111, 222])

    def test_add_tracks_missing_params(self, client):
        resp = client.post("/beatport/add-tracks", json={"track_ids": [111]})
        assert resp.status_code == 400
