from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from typing import Optional
from urllib.parse import quote as _url_quote

import requests
from dotenv import load_dotenv

load_dotenv()

_BASE_URL = "https://api.beatport.com"
_CLIENT_ID = "eHToND3lsv1Xdpa645DdF4wwBUceBniuKPT2dUB1"
_ACCOUNT_BASE = "https://account.beatport.com"
# Base post-message URI registered for this public client
_POST_MESSAGE_URI = "https://api.beatport.com/v4/auth/o/post-message/"
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "beatport_token.json")
_TOKEN_EXPIRY_BUFFER = 60  # seconds before expiry to trigger refresh

_RELEASE_URL_PATTERN = re.compile(r"beatport\.com/release/[^/]+/(\d+)")


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


def _build_redirect_uri(target_origin: str) -> str:
    """The Beatport post-message page requires a ?target=ORIGIN query
    string to know which origin to postMessage the auth code to."""
    return f"{_POST_MESSAGE_URI}?target={target_origin}"


def _generate_pkce_verifier() -> str:
    """Generate a PKCE code_verifier (43-128 chars, URL-safe)."""
    return secrets.token_urlsafe(64)[:128]


def _pkce_challenge(verifier: str) -> str:
    """Compute the S256 PKCE code_challenge from a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def get_authorize_url(target_origin: str) -> tuple[str, str]:
    """Build the Beatport authorization URL via account.beatport.com.

    Returns (url, code_verifier). The verifier must be stored and
    passed back to exchange_code(). Beatport requires PKCE.
    """
    verifier = _generate_pkce_verifier()
    challenge = _pkce_challenge(verifier)
    redirect_uri = _build_redirect_uri(target_origin)
    params = {
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    qs = "&".join(f"{k}={_url_quote(str(v), safe='')}" for k, v in params.items())
    return f"{_ACCOUNT_BASE}/o/authorize/?{qs}", verifier


def exchange_code(code: str, target_origin: str, code_verifier: str) -> dict:
    """Exchange an authorization code for access + refresh tokens.

    The redirect_uri must match what was sent to authorize (including
    ?target=ORIGIN). The code_verifier is the PKCE verifier returned
    by get_authorize_url().
    """
    redirect_uri = _build_redirect_uri(target_origin)
    token_resp = requests.post(
        f"{_ACCOUNT_BASE}/o/token/",
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        auth=(_CLIENT_ID, ""),
    )
    if not token_resp.ok:
        raise RuntimeError(
            f"Beatport token exchange failed: {token_resp.status_code} {token_resp.text}"
        )
    token_data = token_resp.json()

    if "expires_at" not in token_data and "expires_in" in token_data:
        token_data["expires_at"] = time.time() + token_data["expires_in"]

    _save_token(token_data)
    return token_data


def _refresh_access_token(refresh_token: str) -> dict:
    """Use refresh token to obtain a new access token."""
    resp = requests.post(
        f"{_ACCOUNT_BASE}/o/token/",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(_CLIENT_ID, ""),
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
