from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

_BASE_URL = "https://api.beatport.com"
_API_V4 = "https://api.beatport.com/v4"
# This redirect_uri isn't actually used as a real redirect — we set
# allow_redirects=False on authorize and pull the code from Location.
_REDIRECT_URI = f"{_API_V4}/auth/o/post-message/"
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "beatport_token.json")
_TOKEN_EXPIRY_BUFFER = 60  # seconds before expiry to trigger refresh

_RELEASE_URL_PATTERN = re.compile(r"beatport\.com/release/[^/]+/(\d+)")
_SCRIPT_SRC_PATTERN = re.compile(r'src="([^"]*\.js)"')
_CLIENT_ID_PATTERN = re.compile(r"API_CLIENT_ID:\s*'([^']+)'")

_cached_client_id: Optional[str] = None


def _fetch_client_id() -> str:
    """Scrape the Beatport API client_id from the swagger-ui docs page,
    matching the beets-beatport4 approach. The hardcoded client_id
    rotates occasionally; scraping keeps us in sync.
    """
    global _cached_client_id
    if _cached_client_id:
        return _cached_client_id
    html = requests.get(f"{_API_V4}/docs/", timeout=10).text
    for path in _SCRIPT_SRC_PATTERN.findall(html):
        url = f"{_BASE_URL}{path}" if path.startswith("/") else path
        try:
            js = requests.get(url, timeout=10).text
        except requests.RequestException:
            continue
        m = _CLIENT_ID_PATTERN.search(js)
        if m:
            _cached_client_id = m.group(1)
            return _cached_client_id
    raise RuntimeError("Could not scrape Beatport API_CLIENT_ID from docs")


def _load_cached_token() -> Optional[dict]:
    try:
        with open(_TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    env_refresh = os.environ.get("BEATPORT_REFRESH_TOKEN")
    if env_refresh:
        return {"refresh_token": env_refresh, "expires_at": 0}
    return None


def _save_token(token_data: dict) -> None:
    with open(_TOKEN_FILE, "w") as f:
        json.dump(token_data, f)


def _token_is_valid(token_data: dict) -> bool:
    expires_at = token_data.get("expires_at", 0)
    return time.time() < (expires_at - _TOKEN_EXPIRY_BUFFER)


def login_with_password(username: str, password: str) -> dict:
    """Server-side OAuth flow using username + password.

    Logs in via /v4/auth/login/ to get a session cookie, hits the
    authorize endpoint within that session to get an auth code from
    the redirect Location, then exchanges the code for tokens. This
    is how the beets-beatport4 plugin authorizes — no PKCE, no
    client_secret, no browser interaction.
    """
    client_id = _fetch_client_id()
    with requests.Session() as s:
        # 1. Log in to establish a session
        resp = s.post(
            f"{_API_V4}/auth/login/",
            json={"username": username, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        if "username" not in data or "email" not in data:
            raise RuntimeError(f"Beatport login failed: {data}")

        # 2. Hit the authorize endpoint — the auth code arrives in the
        # 302 redirect's Location header (we don't follow it).
        resp = s.get(
            f"{_API_V4}/auth/o/authorize/",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": _REDIRECT_URI,
            },
            allow_redirects=False,
        )
        location = resp.headers.get("Location", "")
        if not location:
            raise RuntimeError(
                f"Beatport authorize returned no Location header "
                f"(status={resp.status_code}, body={resp.text[:200]})"
            )
        # The Location can be a relative path; parse_qs handles both.
        from urllib.parse import urlparse, parse_qs
        codes = parse_qs(urlparse(location).query).get("code")
        if not codes:
            raise RuntimeError(f"No code in authorize redirect: {location}")
        code = codes[0]

        # 3. Exchange the code for tokens. Beatport's /v4/auth/o/token/
        # expects params in the URL query string (not the body).
        resp = s.post(
            f"{_API_V4}/auth/o/token/",
            params={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _REDIRECT_URI,
                "client_id": client_id,
            },
        )
        resp.raise_for_status()
        token_data = resp.json()
        if "error" in token_data or "access_token" not in token_data:
            raise RuntimeError(f"Beatport token exchange failed: {token_data}")

        if "expires_at" not in token_data and "expires_in" in token_data:
            token_data["expires_at"] = time.time() + token_data["expires_in"]

        _save_token(token_data)
        return token_data


def _refresh_access_token(refresh_token: str) -> dict:
    """Use refresh token to obtain a new access token."""
    resp = requests.post(
        f"{_API_V4}/auth/o/token/",
        params={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _fetch_client_id(),
        },
    )
    resp.raise_for_status()
    token_data = resp.json()

    if "expires_at" not in token_data and "expires_in" in token_data:
        token_data["expires_at"] = time.time() + token_data["expires_in"]

    _save_token(token_data)
    return token_data


def is_authenticated() -> bool:
    """Check whether we have valid Beatport user credentials."""
    token_data = _load_cached_token()
    if not token_data:
        return False
    if _token_is_valid(token_data):
        return True
    # Check if we can refresh
    return bool(token_data.get("refresh_token"))


def _get_valid_token() -> str:
    """Get a valid access token, refreshing as needed."""
    token_data = _load_cached_token()

    if token_data and _token_is_valid(token_data):
        return token_data["access_token"]

    # Try refresh
    if token_data and token_data.get("refresh_token"):
        try:
            token_data = _refresh_access_token(token_data["refresh_token"])
            return token_data["access_token"]
        except requests.HTTPError:
            pass

    raise RuntimeError(
        "Beatport not authenticated — click 'Connect Beatport' first"
    )


def _api_request(method: str, path: str, **kwargs) -> requests.Response:
    """Make an authenticated request to the Beatport API."""
    token = _get_valid_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(
        method,
        f"{_BASE_URL}{path}",
        headers=headers,
        **kwargs,
    )
    resp.raise_for_status()
    return resp


def extract_release_id(beatport_url: str) -> Optional[int]:
    """Extract the numeric release ID from a Beatport release URL."""
    match = _RELEASE_URL_PATTERN.search(beatport_url)
    if match:
        return int(match.group(1))
    return None


def get_release_tracks(beatport_url: str) -> list[dict]:
    """Resolve a Beatport release URL to a list of full track dicts via
    the authenticated API. Returns [] for non-release URLs or when not
    authenticated.

    Each track has at least: id, name, mix_name, and artists (list of
    {id, name, ...}).
    """
    release_id = extract_release_id(beatport_url)
    if release_id is None:
        return []
    try:
        resp = _api_request("GET", f"/v4/catalog/releases/{release_id}/tracks/")
    except RuntimeError:
        return []
    data = resp.json()
    tracks = data.get("results", data) if isinstance(data, dict) else data
    return tracks or []


def get_track_ids(beatport_url: str) -> list[int]:
    """Resolve a Beatport release URL to a list of track IDs."""
    return [t["id"] for t in get_release_tracks(beatport_url) if "id" in t]


def create_playlist(name: str) -> dict:
    """Create a new Beatport playlist. Returns {id, name}."""
    resp = _api_request("POST", "/v4/my/playlists/", json={"name": name})
    data = resp.json()
    return {"id": data["id"], "name": data.get("name", name)}


def get_my_playlists() -> list[dict]:
    """Get the authenticated user's playlists."""
    resp = _api_request("GET", "/v4/my/playlists/")
    data = resp.json()
    results = data.get("results", data) if isinstance(data, dict) else data
    return [{"id": p["id"], "name": p.get("name", f"Playlist {p['id']}")} for p in results]


def add_tracks_to_playlist(playlist_id: int, track_ids: list[int]) -> dict:
    """Add tracks to a Beatport playlist."""
    resp = _api_request(
        "POST",
        f"/v4/my/playlists/{playlist_id}/tracks/bulk/",
        json={"track_ids": track_ids},
    )
    return resp.json()
