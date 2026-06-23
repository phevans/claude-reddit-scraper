from __future__ import annotations

import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
_SCOPES = "playlist-modify-public playlist-modify-private"
# Token cache lives in TOKEN_DIR when set (e.g. /tmp on Lambda, whose
# app dir is read-only) else next to the code. Either way it's only a
# warm-cache; SPOTIFY_REFRESH_TOKEN re-bootstraps auth on a cold start.
_TOKEN_FILE = os.path.join(
    os.environ.get("TOKEN_DIR") or os.path.dirname(__file__), "spotify_token.json"
)
_TOKEN_EXPIRY_BUFFER = 60

_user_token_cache: dict = {}


def get_authorize_url(redirect_uri: str) -> str:
    """Build the Spotify authorization URL for the user to visit."""
    params = {
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
    }
    qs = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"https://accounts.spotify.com/authorize?{qs}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        auth=(_CLIENT_ID, _CLIENT_SECRET),
    )
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)
    _save_token(token_data)
    _user_token_cache.update(token_data)
    return token_data


def _save_token(token_data: dict) -> None:
    _user_token_cache.update(token_data)
    try:
        with open(_TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
    except OSError:
        # Read-only filesystem (e.g. Lambda /var/task). The in-memory
        # cache above serves the warm container; cold starts re-bootstrap
        # from SPOTIFY_REFRESH_TOKEN, so a failed write is non-fatal.
        pass


def _load_cached_token() -> dict | None:
    if _user_token_cache.get("access_token"):
        return dict(_user_token_cache)
    try:
        with open(_TOKEN_FILE) as f:
            data = json.load(f)
            _user_token_cache.update(data)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Fall back to env var (for EB where token file doesn't persist)
    env_refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    if env_refresh:
        return {"refresh_token": env_refresh, "expires_at": 0}
    return None


def _refresh_access_token(refresh_token: str) -> dict:
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(_CLIENT_ID, _CLIENT_SECRET),
    )
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)
    # Spotify may or may not return a new refresh_token
    if "refresh_token" not in token_data:
        token_data["refresh_token"] = refresh_token
    _save_token(token_data)
    _user_token_cache.update(token_data)
    return token_data


def _get_user_token() -> str | None:
    """Get a valid user access token, refreshing if needed. Returns None if not authenticated."""
    token_data = _load_cached_token()
    if not token_data:
        return None

    expires_at = token_data.get("expires_at", 0)
    if time.time() < expires_at - _TOKEN_EXPIRY_BUFFER:
        return token_data["access_token"]

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return None

    refreshed = _refresh_access_token(refresh_token)
    return refreshed["access_token"]


def is_authenticated() -> bool:
    """Check whether we have valid Spotify user credentials."""
    return _get_user_token() is not None


# Retry budget for transient failures (network errors, 5xx, 429). The
# 401-refresh attempt is separate and doesn't consume this budget.
_MAX_RETRIES = 3
# Hard ceiling on a single sleep, even if Retry-After is enormous.
_MAX_BACKOFF_SECONDS = 30


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 1s, 2s, 4s, ... capped at _MAX_BACKOFF_SECONDS."""
    return min(2 ** attempt, _MAX_BACKOFF_SECONDS)


def _api_request(method: str, url: str, **kwargs) -> requests.Response:
    """Make an authenticated request to the Spotify API.

    Handles three retry concerns:
      - 401: refresh the cached token and retry once (free, not counted
        against the transient-error budget).
      - 429: honour Retry-After (capped at _MAX_BACKOFF_SECONDS).
      - 5xx / connection errors: exponential backoff up to _MAX_RETRIES.

    Real 4xx errors (other than 401/429) and exhausted retries return
    the response as-is for the caller to `raise_for_status()` against.
    """
    refreshed_once = False
    for attempt in range(_MAX_RETRIES + 1):
        token = _get_user_token()
        if not token:
            raise RuntimeError("Spotify user not authenticated")
        try:
            response = requests.request(
                method, url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                **kwargs,
            )
        except (requests.ConnectionError, requests.Timeout):
            if attempt >= _MAX_RETRIES:
                raise
            time.sleep(_backoff_seconds(attempt))
            continue

        if response.status_code == 401 and not refreshed_once:
            _user_token_cache.clear()
            refreshed_once = True
            # Don't count this against the retry budget — it's a token
            # bookkeeping concern, not a transient API failure.
            continue

        if response.status_code == 429 and attempt < _MAX_RETRIES:
            # Spotify documents Retry-After in seconds. Fall back to
            # exponential backoff if the header is absent or garbage.
            try:
                wait = float(response.headers.get("Retry-After", ""))
            except ValueError:
                wait = _backoff_seconds(attempt)
            time.sleep(min(wait, _MAX_BACKOFF_SECONDS))
            continue

        if 500 <= response.status_code < 600 and attempt < _MAX_RETRIES:
            time.sleep(_backoff_seconds(attempt))
            continue

        return response

    return response


def get_user_id() -> str:
    """Get the current authenticated user's Spotify ID."""
    resp = _api_request("GET", "https://api.spotify.com/v1/me")
    resp.raise_for_status()
    return resp.json()["id"]


def create_playlist(name: str, description: str = "", public: bool = False) -> dict:
    """Create a new Spotify playlist. Returns {id, name, url}."""
    user_id = get_user_id()
    resp = _api_request("POST", f"https://api.spotify.com/v1/users/{user_id}/playlists",
                        json={"name": name, "description": description, "public": public})
    resp.raise_for_status()
    data = resp.json()
    return {
        "id": data["id"],
        "name": data["name"],
        "url": data.get("external_urls", {}).get("spotify", ""),
    }


def _parse_spotify_url(url: str) -> tuple[str, str] | None:
    """Extract (type, id) from a Spotify URL."""
    match = re.search(r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


def extract_playlist_id(url: str) -> str | None:
    """Extract the playlist ID from an open.spotify.com playlist URL."""
    parsed = _parse_spotify_url(url)
    if parsed and parsed[0] == "playlist":
        return parsed[1]
    return None


def get_album_track_uris(album_id: str) -> list[str]:
    """Get all track URIs from a Spotify album."""
    uris = []
    url = f"https://api.spotify.com/v1/albums/{album_id}/tracks?limit=50"
    while url:
        resp = _api_request("GET", url)
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get("items", []):
            uris.append(item["uri"])
        url = data.get("next")
    return uris


def resolve_track_uris(spotify_url: str) -> list[str]:
    """Resolve a Spotify URL (album or track) to a list of track URIs."""
    parsed = _parse_spotify_url(spotify_url)
    if not parsed:
        return []
    resource_type, resource_id = parsed
    if resource_type == "track":
        return [f"spotify:track:{resource_id}"]
    elif resource_type == "album":
        return get_album_track_uris(resource_id)
    return []


def add_tracks_to_playlist(playlist_id: str, track_uris: list[str]) -> dict:
    """Add tracks to a Spotify playlist. Handles batching (max 100 per request)."""
    added = 0
    for i in range(0, len(track_uris), 100):
        batch = track_uris[i:i + 100]
        resp = _api_request("POST", f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                            json={"uris": batch})
        resp.raise_for_status()
        added += len(batch)
    return {"added": added}


def get_playlist(playlist_id: str) -> dict:
    """Fetch playlist metadata. Returns {id, name, url}."""
    resp = _api_request("GET", f"https://api.spotify.com/v1/playlists/{playlist_id}",
                        params={"fields": "id,name,external_urls"})
    resp.raise_for_status()
    data = resp.json()
    return {
        "id": data["id"],
        "name": data.get("name", ""),
        "url": data.get("external_urls", {}).get("spotify", ""),
    }


def get_playlist_track_uris(playlist_id: str) -> list[str]:
    """Return every track URI in a playlist, paginated."""
    uris: list[str] = []
    url = (f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
           "?fields=items(track(uri)),next&limit=100")
    while url:
        resp = _api_request("GET", url)
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get("items", []):
            track = item.get("track") or {}
            uri = track.get("uri")
            # Local files and removed tracks have no usable URI; skip them.
            if uri and uri.startswith("spotify:track:"):
                uris.append(uri)
        url = data.get("next")
    return uris


def _get_playlist_state(playlist_id: str) -> dict:
    """Capture (snapshot_id, items-with-positions) for a playlist.

    Items include every entry that has a URI, with its position in the
    playlist. This is precision input for snapshot-based deletes — we
    don't filter out non-track URIs here because their positions still
    matter for the math.
    """
    meta_resp = _api_request("GET",
                             f"https://api.spotify.com/v1/playlists/{playlist_id}",
                             params={"fields": "snapshot_id"})
    meta_resp.raise_for_status()
    snapshot_id = meta_resp.json().get("snapshot_id", "")

    items: list[tuple[str, int]] = []
    pos = 0
    url = (f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
           "?fields=items(track(uri)),next&limit=100")
    while url:
        resp = _api_request("GET", url)
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get("items", []):
            uri = (item.get("track") or {}).get("uri")
            if uri:
                items.append((uri, pos))
            pos += 1  # increment even for null-URI rows; positions are absolute
        url = data.get("next")
    return {"snapshot_id": snapshot_id, "items": items}


def _remove_tracks_by_position(playlist_id: str,
                               items: list[tuple[str, int]],
                               snapshot_id: str) -> None:
    """Remove tracks at specific positions using a captured snapshot_id.

    Spotify's DELETE accepts up to 100 entries in `tracks`; we group
    positions by URI within each batch in case the same URI sits at
    multiple positions. The snapshot_id pins the meaning of those
    positions even after other modifications (our POSTs in the
    surrounding flow append at the tail, so the snapshot's positions
    still identify the right items).
    """
    if not items:
        return
    for i in range(0, len(items), 100):
        batch = items[i:i + 100]
        by_uri: dict[str, list[int]] = {}
        for uri, position in batch:
            by_uri.setdefault(uri, []).append(position)
        payload = {
            "tracks": [{"uri": uri, "positions": positions}
                       for uri, positions in by_uri.items()],
            "snapshot_id": snapshot_id,
        }
        resp = _api_request("DELETE",
                            f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                            json=payload)
        resp.raise_for_status()


def replace_playlist_tracks(playlist_id: str, track_uris: list[str]) -> dict:
    """Replace the entire contents of a playlist with `track_uris`.

    Uses an add-then-delete sequence (rather than the obvious
    PUT-clear-then-POST) so that the playlist is never strictly worse
    than its starting state during the operation. The worst possible
    failure mode is leftover old tracks alongside the new ones, which
    is visible and recoverable; the PUT approach can produce silent
    *missing* tracks if a later batch fails.

    Sequence:
      1. Capture (snapshot_id, items-with-positions) of the current
         playlist. Items at indices [0..N-1] are the "old" set.
      2. POST track_uris in 100-batches → playlist is now
         [old..., new...]. If a batch fails the playlist is in a
         non-destructive intermediate state and the exception
         propagates.
      3. DELETE the old items by snapshot+position in 100-batches.
         The snapshot_id ensures Spotify resolves positions against
         the pre-modification state, so concurrent edits don't make
         us delete the wrong things.

    The caller's safety net is the dated backup playlist created
    upstream — if anything raises here, that backup is intact.
    """
    state = _get_playlist_state(playlist_id)
    old_items = state["items"]
    snapshot_id = state["snapshot_id"]

    if track_uris:
        add_tracks_to_playlist(playlist_id, track_uris)

    _remove_tracks_by_position(playlist_id, old_items, snapshot_id)

    return {"replaced": len(track_uris)}


def delete_playlist(playlist_id: str) -> None:
    """Unfollow (effectively delete for the owning user) a playlist."""
    resp = _api_request("DELETE",
                        f"https://api.spotify.com/v1/playlists/{playlist_id}/followers")
    resp.raise_for_status()


def get_tracks_info(track_uris: list[str]) -> list[dict]:
    """Fetch (uri, name, artists) for each track URI.

    Used by the preview flow: a list of URIs alone isn't reviewable —
    the user needs to see what they're about to add or remove.
    Batches at 50 per Spotify's documented cap.

    Tracks that can't be resolved (deleted from catalogue, region-locked,
    etc.) come back with name=None / artists=None so the UI can render
    them as "<unknown — spotify:track:xxxxx>" rather than silently
    disappearing from the diff.
    """
    by_uri: dict[str, dict] = {u: {"uri": u, "name": None, "artists": None}
                               for u in track_uris}
    # Pull track IDs from URIs; preserve URIs that aren't spotify:track:
    # (e.g. spotify:local:) as unresolved entries.
    ids = [u.split(":")[-1] for u in track_uris if u.startswith("spotify:track:")]
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        resp = _api_request("GET", "https://api.spotify.com/v1/tracks",
                            params={"ids": ",".join(batch)})
        resp.raise_for_status()
        for t in resp.json().get("tracks", []) or []:
            if not t:
                continue
            uri = t.get("uri")
            if not uri or uri not in by_uri:
                continue
            by_uri[uri]["name"] = t.get("name")
            by_uri[uri]["artists"] = ", ".join(
                a.get("name", "") for a in t.get("artists", []) if isinstance(a, dict)
            )
    # Preserve caller's order.
    return [by_uri[u] for u in track_uris]


def find_user_playlists_by_name_prefix(prefix: str) -> list[dict]:
    """List the authenticated user's playlists whose name starts with
    `prefix`. Paginates through /v1/me/playlists. Returns [{id, name}].
    """
    matches: list[dict] = []
    url = "https://api.spotify.com/v1/me/playlists?limit=50"
    while url:
        resp = _api_request("GET", url)
        if resp.status_code != 200:
            break
        data = resp.json()
        for p in data.get("items", []):
            name = p.get("name", "") or ""
            if name.startswith(prefix):
                matches.append({"id": p["id"], "name": name})
        url = data.get("next")
    return matches
