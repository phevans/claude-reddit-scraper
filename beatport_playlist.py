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
_CLIENT_ID = "eHToND3lsv1Xdpa645DdF4wwBUceBniuKPT2dUB1"
_REDIRECT_URI = "https://api.beatport.com/auth/o/post-message/"
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "beatport_token.json")
_TOKEN_EXPIRY_BUFFER = 60  # seconds before expiry to trigger refresh

_RELEASE_URL_PATTERN = re.compile(r"beatport\.com/release/[^/]+/(\d+)")


def _load_cached_token() -> Optional[dict]:
    try:
        with open(_TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_token(token_data: dict) -> None:
    with open(_TOKEN_FILE, "w") as f:
        json.dump(token_data, f)


def _token_is_valid(token_data: dict) -> bool:
    expires_at = token_data.get("expires_at", 0)
    return time.time() < (expires_at - _TOKEN_EXPIRY_BUFFER)


def _authenticate(username: str, password: str) -> dict:
    """Login to Beatport and obtain OAuth tokens via authorization_code flow."""
    session = requests.Session()

    # Step 1: Login to get session cookies
    login_resp = session.get(f"{_BASE_URL}/auth/login/")
    csrf_token = session.cookies.get("csrftoken", "")

    login_resp = session.post(
        f"{_BASE_URL}/auth/login/",
        data={
            "username": username,
            "password": password,
            "csrfmiddlewaretoken": csrf_token,
        },
        headers={
            "Referer": f"{_BASE_URL}/auth/login/",
        },
        allow_redirects=True,
    )
    if login_resp.status_code not in (200, 302):
        raise RuntimeError(f"Beatport login failed: {login_resp.status_code}")

    # Step 2: Request authorization code
    auth_resp = session.get(
        f"{_BASE_URL}/auth/o/authorize/",
        params={
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
        },
        allow_redirects=False,
    )

    # The redirect URL contains the authorization code
    if auth_resp.status_code in (301, 302):
        location = auth_resp.headers.get("Location", "")
        code_match = re.search(r"[?&]code=([^&]+)", location)
        if not code_match:
            raise RuntimeError(f"No auth code in redirect: {location}")
        auth_code = code_match.group(1)
    else:
        # Some flows return the code in the response body
        code_match = re.search(r'"code"\s*:\s*"([^"]+)"', auth_resp.text)
        if not code_match:
            raise RuntimeError(
                f"Could not obtain authorization code (status {auth_resp.status_code})"
            )
        auth_code = code_match.group(1)

    # Step 3: Exchange code for tokens
    token_resp = requests.post(
        f"{_BASE_URL}/v4/auth/o/token/",
        data={
            "code": auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
            "client_id": _CLIENT_ID,
        },
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()

    # Calculate expires_at if not provided
    if "expires_at" not in token_data and "expires_in" in token_data:
        token_data["expires_at"] = time.time() + token_data["expires_in"]

    _save_token(token_data)
    return token_data


def _refresh_access_token(refresh_token: str) -> dict:
    """Use refresh token to obtain a new access token."""
    resp = requests.post(
        f"{_BASE_URL}/v4/auth/o/token/",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
        },
    )
    resp.raise_for_status()
    token_data = resp.json()

    if "expires_at" not in token_data and "expires_in" in token_data:
        token_data["expires_at"] = time.time() + token_data["expires_in"]

    _save_token(token_data)
    return token_data


def _get_valid_token() -> str:
    """Get a valid access token, refreshing or re-authenticating as needed."""
    token_data = _load_cached_token()

    if token_data and _token_is_valid(token_data):
        return token_data["access_token"]

    # Try refresh
    if token_data and token_data.get("refresh_token"):
        try:
            token_data = _refresh_access_token(token_data["refresh_token"])
            return token_data["access_token"]
        except requests.HTTPError:
            pass  # Fall through to re-authenticate

    # Full re-authentication
    username = os.environ.get("BEATPORT_USERNAME")
    password = os.environ.get("BEATPORT_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Beatport token expired and no BEATPORT_USERNAME/BEATPORT_PASSWORD "
            "set for re-authentication"
        )
    token_data = _authenticate(username, password)
    return token_data["access_token"]


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


def get_track_ids(beatport_url: str) -> list[int]:
    """Resolve a Beatport release URL to a list of track IDs."""
    release_id = extract_release_id(beatport_url)
    if release_id is None:
        return []

    resp = _api_request("GET", f"/v4/catalog/releases/{release_id}/tracks/")
    data = resp.json()

    # Handle paginated response (results key) or direct list
    tracks = data.get("results", data) if isinstance(data, dict) else data
    return [t["id"] for t in tracks if "id" in t]


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
