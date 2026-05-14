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
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "spotify_token.json")
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
    with open(_TOKEN_FILE, "w") as f:
        json.dump(token_data, f)


def _load_cached_token() -> dict | None:
    if _user_token_cache.get("access_token"):
        return dict(_user_token_cache)
    try:
        with open(_TOKEN_FILE) as f:
            data = json.load(f)
            _user_token_cache.update(data)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
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


def _api_request(method: str, url: str, **kwargs) -> requests.Response:
    """Make an authenticated request to the Spotify API with retry on 401."""
    for attempt in range(2):
        token = _get_user_token()
        if not token:
            raise RuntimeError("Spotify user not authenticated")
        response = requests.request(
            method, url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            **kwargs,
        )
        if response.status_code == 401 and attempt == 0:
            _user_token_cache.clear()
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
    match = re.search(r"open\.spotify\.com/(track|album)/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1), match.group(2)
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
