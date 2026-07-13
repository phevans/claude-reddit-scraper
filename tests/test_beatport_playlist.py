import json
import time
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

from beatport_playlist import (
    _load_cached_token,
    _save_token,
    _token_is_valid,
    add_tracks_to_playlist,
    extract_release_id,
    get_release,
    get_release_tracks,
    get_track,
    get_track_ids,
)


class TestSaveTokenReadOnlyFs:
    @patch("builtins.open", side_effect=OSError("Read-only file system"))
    def test_save_token_swallows_readonly_error(self, _open):
        # On Lambda the app dir is read-only; a failed write must not crash.
        _save_token({"refresh_token": "x", "expires_at": 0})


def _error_response(status_code=404):
    """A non-OK requests.Response stand-in: drives _api_request to raise
    requests.HTTPError, the way a wrong/deleted Beatport ID does."""
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status_code
    resp.reason = "Not Found"
    resp.url = "https://api.beatport.com/v4/catalog/releases/0/"
    resp.text = '{"detail": "Not found."}'
    return resp


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


class TestIsAuthenticated:
    """is_authenticated validates the token rather than just checking it
    exists — a dead/revoked refresh token must report False so Beatport
    failures surface instead of silently breaking the run."""

    @patch("beatport_playlist._get_valid_token", return_value="access-tok")
    def test_true_when_token_obtainable(self, _mock):
        from beatport_playlist import is_authenticated
        assert is_authenticated() is True

    @patch("beatport_playlist._get_valid_token",
           side_effect=RuntimeError("not authenticated"))
    def test_false_when_refresh_fails(self, _mock):
        from beatport_playlist import is_authenticated
        assert is_authenticated() is False

    @patch("beatport_playlist._get_valid_token",
           side_effect=requests.RequestException("network"))
    def test_false_on_network_error(self, _mock):
        from beatport_playlist import is_authenticated
        assert is_authenticated() is False


class TestBeatportCredentials:
    """(username, password) resolution for the headless-login bootstrap:
    username from a plain env var, password from an env var locally or an
    SSM SecureString in prod."""

    def setup_method(self):
        import beatport_playlist
        beatport_playlist._cached_password = None

    @patch.dict("os.environ", {"BEATPORT_USERNAME": "", "BEATPORT_PASSWORD": ""},
                clear=False)
    def test_none_without_username(self):
        from beatport_playlist import _beatport_credentials
        with patch.dict("os.environ", {}, clear=True):
            assert _beatport_credentials() == (None, None)

    def test_uses_env_password(self):
        from beatport_playlist import _beatport_credentials
        with patch.dict("os.environ",
                        {"BEATPORT_USERNAME": "me", "BEATPORT_PASSWORD": "pw"},
                        clear=True):
            assert _beatport_credentials() == ("me", "pw")

    @patch("beatport_playlist._read_ssm_secure", return_value="ssm-pw")
    def test_falls_back_to_ssm(self, mock_ssm):
        from beatport_playlist import _beatport_credentials
        with patch.dict("os.environ",
                        {"BEATPORT_USERNAME": "me",
                         "BEATPORT_PASSWORD_SSM": "/dnb-scraper/beatport-password"},
                        clear=True):
            assert _beatport_credentials() == ("me", "ssm-pw")
        mock_ssm.assert_called_once_with("/dnb-scraper/beatport-password")

    @patch("beatport_playlist._read_ssm_secure", return_value="ssm-pw")
    def test_caches_ssm_password(self, mock_ssm):
        from beatport_playlist import _beatport_credentials
        with patch.dict("os.environ",
                        {"BEATPORT_USERNAME": "me",
                         "BEATPORT_PASSWORD_SSM": "/p"}, clear=True):
            _beatport_credentials()
            _beatport_credentials()
        mock_ssm.assert_called_once()  # second call served from cache


class TestGetValidTokenLoginFallback:
    """When no cached/refreshable token is available, _get_valid_token
    mints one via a fresh headless login (rotation-proof bootstrap)."""

    @patch("beatport_playlist.login_with_password",
           return_value={"access_token": "fresh-tok"})
    @patch("beatport_playlist._beatport_credentials", return_value=("me", "pw"))
    @patch("beatport_playlist._load_cached_token", return_value=None)
    def test_logs_in_when_no_token(self, _load, _creds, mock_login):
        from beatport_playlist import _get_valid_token
        assert _get_valid_token() == "fresh-tok"
        mock_login.assert_called_once_with("me", "pw")

    @patch("beatport_playlist.login_with_password",
           return_value={"access_token": "fresh-tok"})
    @patch("beatport_playlist._beatport_credentials", return_value=("me", "pw"))
    @patch("beatport_playlist._refresh_access_token",
           side_effect=requests.HTTPError("400"))
    @patch("beatport_playlist._load_cached_token",
           return_value={"refresh_token": "spent", "expires_at": 0})
    def test_falls_back_to_login_when_refresh_dies(
        self, _load, _refresh, _creds, mock_login
    ):
        # The exact prod failure: env-seeded refresh token is spent (400),
        # so fall through to a fresh login instead of raising.
        from beatport_playlist import _get_valid_token
        assert _get_valid_token() == "fresh-tok"
        mock_login.assert_called_once()

    @patch("beatport_playlist._beatport_credentials", return_value=(None, None))
    @patch("beatport_playlist._load_cached_token", return_value=None)
    def test_raises_without_credentials(self, _load, _creds):
        from beatport_playlist import _get_valid_token
        with pytest.raises(RuntimeError, match="not authenticated"):
            _get_valid_token()


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


class TestGettersDegradeOnBadUrl:
    """An incorrect Beatport URL whose ID still matches the regex returns a
    404 from the API. The catalog getters must degrade to None/[] rather
    than let the HTTPError abort the entire scrape (one bad link killed
    the whole run)."""

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_get_track_404_returns_none(self, mock_request, mock_token):
        mock_request.return_value = _error_response(404)
        assert get_track(99999999) is None

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_get_release_404_returns_none(self, mock_request, mock_token):
        mock_request.return_value = _error_response(404)
        assert get_release(99999999) is None

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request")
    def test_get_release_tracks_404_returns_empty(self, mock_request, mock_token):
        mock_request.return_value = _error_response(404)
        url = "https://www.beatport.com/release/gone/99999999"
        assert get_release_tracks(url) == []

    @patch("beatport_playlist._get_valid_token", return_value="test_token")
    @patch("beatport_playlist.requests.request",
           side_effect=requests.ConnectionError("boom"))
    def test_get_release_network_error_returns_none(self, mock_request, mock_token):
        # A transient network failure must also degrade, not crash.
        assert get_release(123) is None


