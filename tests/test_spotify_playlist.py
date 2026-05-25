"""Tests for spotify_playlist's lower-level pieces: the resilient
_api_request retry layer and the add-then-delete replace_playlist_tracks
flow.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

import spotify_playlist
from spotify_playlist import (
    _api_request,
    _backoff_seconds,
    _MAX_RETRIES,
    replace_playlist_tracks,
)


def _resp(status: int, headers: dict | None = None, json_body=None) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = json_body if json_body is not None else {}
    return r


class TestBackoffSchedule:
    def test_exponential_until_cap(self):
        assert _backoff_seconds(0) == 1
        assert _backoff_seconds(1) == 2
        assert _backoff_seconds(2) == 4
        # Capped at _MAX_BACKOFF_SECONDS (30) — large attempt counts
        # don't blow past it.
        assert _backoff_seconds(20) == 30


class TestApiRequestRetries:
    """All tests below patch `time.sleep` so the retry loop is instant."""

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_success_first_try_no_retry(self, _tok, mock_req, mock_sleep):
        mock_req.return_value = _resp(200)
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 200
        assert mock_req.call_count == 1
        mock_sleep.assert_not_called()

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_401_refreshes_then_succeeds_without_consuming_retry_budget(
        self, _tok, mock_req, mock_sleep
    ):
        # 401 once, then 200. The token refresh isn't counted against
        # the transient-error budget — verified by absence of sleep().
        mock_req.side_effect = [_resp(401), _resp(200)]
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 200
        assert mock_req.call_count == 2
        mock_sleep.assert_not_called()

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_429_honours_retry_after_header(self, _tok, mock_req, mock_sleep):
        mock_req.side_effect = [
            _resp(429, headers={"Retry-After": "7"}),
            _resp(200),
        ]
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 200
        mock_sleep.assert_called_once_with(7.0)

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_429_caps_retry_after_at_max(self, _tok, mock_req, mock_sleep):
        # An evil Retry-After shouldn't park us for an hour.
        mock_req.side_effect = [
            _resp(429, headers={"Retry-After": "9999"}),
            _resp(200),
        ]
        _api_request("GET", "https://api.spotify.com/v1/me")
        mock_sleep.assert_called_once_with(spotify_playlist._MAX_BACKOFF_SECONDS)

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_5xx_exponential_backoff(self, _tok, mock_req, mock_sleep):
        mock_req.side_effect = [_resp(503), _resp(502), _resp(200)]
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 200
        # 1s then 2s — the documented exponential schedule.
        assert [c.args[0] for c in mock_sleep.call_args_list] == [1, 2]

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_connection_error_retried_then_succeeds(
        self, _tok, mock_req, mock_sleep
    ):
        mock_req.side_effect = [
            requests.ConnectionError("flake"),
            _resp(200),
        ]
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 200
        assert mock_sleep.call_count == 1

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_persistent_5xx_returns_last_response_for_caller(
        self, _tok, mock_req, mock_sleep
    ):
        # All attempts 5xx → return the final response. The caller
        # decides whether to raise_for_status.
        mock_req.return_value = _resp(503)
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 503
        # Initial attempt + _MAX_RETRIES retries.
        assert mock_req.call_count == _MAX_RETRIES + 1

    @patch("spotify_playlist.time.sleep")
    @patch("spotify_playlist.requests.request")
    @patch("spotify_playlist._get_user_token", return_value="tok")
    def test_4xx_other_than_401_429_not_retried(
        self, _tok, mock_req, mock_sleep
    ):
        # 400 is a real error: don't waste budget retrying.
        mock_req.return_value = _resp(400)
        resp = _api_request("GET", "https://api.spotify.com/v1/me")
        assert resp.status_code == 400
        assert mock_req.call_count == 1
        mock_sleep.assert_not_called()


class TestReplacePlaylistTracksAddThenDelete:
    """The new add-then-delete sequence: at every moment during the
    operation the playlist contains at least the right tracks. Worst
    failure leaves extras, never missing.
    """

    def test_empty_old_playlist_just_adds(self):
        # Empty playlist → no DELETE called.
        with patch("spotify_playlist._get_playlist_state",
                   return_value={"snapshot_id": "snap", "items": []}), \
             patch("spotify_playlist.add_tracks_to_playlist") as mock_add, \
             patch("spotify_playlist._remove_tracks_by_position") as mock_del:
            result = replace_playlist_tracks("PL", ["spotify:track:A", "spotify:track:B"])

        mock_add.assert_called_once_with("PL", ["spotify:track:A", "spotify:track:B"])
        mock_del.assert_called_once_with("PL", [], "snap")
        assert result == {"replaced": 2}

    def test_existing_tracks_replaced_with_new_via_add_then_delete(self):
        old_items = [("spotify:track:OLD1", 0), ("spotify:track:OLD2", 1)]
        new = ["spotify:track:NEW1", "spotify:track:NEW2"]

        call_order: list[str] = []

        def record_add(*a, **kw):
            call_order.append("add")

        def record_del(*a, **kw):
            call_order.append("delete")

        with patch("spotify_playlist._get_playlist_state",
                   return_value={"snapshot_id": "snap", "items": old_items}), \
             patch("spotify_playlist.add_tracks_to_playlist",
                   side_effect=record_add) as mock_add, \
             patch("spotify_playlist._remove_tracks_by_position",
                   side_effect=record_del) as mock_del:
            replace_playlist_tracks("PL", new)

        # Critical ordering: ADD must run before DELETE. If reversed,
        # we'd be back to the lossy PUT-style failure mode.
        assert call_order == ["add", "delete"]
        mock_add.assert_called_once_with("PL", new)
        mock_del.assert_called_once_with("PL", old_items, "snap")

    def test_add_failure_aborts_before_any_delete(self):
        """If the POST fails partway, the DELETE never fires — so the
        playlist is left as [old..., partial_new...], never missing
        tracks."""
        old_items = [("spotify:track:OLD1", 0)]
        with patch("spotify_playlist._get_playlist_state",
                   return_value={"snapshot_id": "snap", "items": old_items}), \
             patch("spotify_playlist.add_tracks_to_playlist",
                   side_effect=RuntimeError("api boom")), \
             patch("spotify_playlist._remove_tracks_by_position") as mock_del:
            with pytest.raises(RuntimeError, match="api boom"):
                replace_playlist_tracks("PL", ["spotify:track:NEW1"])
        # The structural guarantee.
        mock_del.assert_not_called()

    def test_empty_new_tracks_just_clears(self):
        old_items = [("spotify:track:OLD1", 0)]
        with patch("spotify_playlist._get_playlist_state",
                   return_value={"snapshot_id": "snap", "items": old_items}), \
             patch("spotify_playlist.add_tracks_to_playlist") as mock_add, \
             patch("spotify_playlist._remove_tracks_by_position") as mock_del:
            result = replace_playlist_tracks("PL", [])

        mock_add.assert_not_called()  # nothing to add
        mock_del.assert_called_once_with("PL", old_items, "snap")
        assert result == {"replaced": 0}


class TestRemoveTracksByPosition:
    @patch("spotify_playlist._api_request")
    def test_batches_at_100_and_groups_by_uri(self, mock_api):
        mock_api.return_value = _resp(200)
        # Same URI appearing at multiple positions exercises the
        # group-by-URI path; the API expects one entry per URI with
        # a `positions` list.
        items = [
            ("spotify:track:A", 0),
            ("spotify:track:A", 5),
            ("spotify:track:B", 1),
        ]
        spotify_playlist._remove_tracks_by_position("PL", items, "snap")
        assert mock_api.call_count == 1
        payload = mock_api.call_args.kwargs["json"]
        assert payload["snapshot_id"] == "snap"
        # Each URI once, with all its positions collected.
        by_uri = {t["uri"]: sorted(t["positions"]) for t in payload["tracks"]}
        assert by_uri == {"spotify:track:A": [0, 5], "spotify:track:B": [1]}

    @patch("spotify_playlist._api_request")
    def test_splits_over_100_into_multiple_requests(self, mock_api):
        mock_api.return_value = _resp(200)
        items = [(f"spotify:track:T{i}", i) for i in range(250)]
        spotify_playlist._remove_tracks_by_position("PL", items, "snap")
        assert mock_api.call_count == 3  # 100 + 100 + 50

    @patch("spotify_playlist._api_request")
    def test_empty_items_makes_no_request(self, mock_api):
        spotify_playlist._remove_tracks_by_position("PL", [], "snap")
        mock_api.assert_not_called()
