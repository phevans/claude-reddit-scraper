import os
import re
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv

load_dotenv()

_token_cache: dict[str, str] = {}


def _get_access_token() -> str:
    """Get a Spotify access token using client credentials flow."""
    if "token" in _token_cache:
        return _token_cache["token"]

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(
            os.environ["SPOTIFY_CLIENT_ID"],
            os.environ["SPOTIFY_CLIENT_SECRET"],
        ),
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    _token_cache["token"] = token
    return token


def _parse_spotify_url(url: str) -> tuple[str, str] | None:
    """Extract (type, id) from a Spotify URL. Type is 'track' or 'album'."""
    match = re.search(r"open\.spotify\.com/(track|album)/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


def _fetch_spotify_name(resource_type: str, resource_id: str) -> str | None:
    """Fetch the name of a track or album from Spotify."""
    token = _get_access_token()
    response = requests.get(
        f"https://api.spotify.com/v1/{resource_type}s/{resource_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code != 200:
        return None
    return response.json().get("name")


def compute_similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def verify_spotify_link(release_title: str, spotify_url: str) -> float | None:
    """Check a Spotify URL against a release title. Returns similarity score or None on failure."""
    parsed = _parse_spotify_url(spotify_url)
    if not parsed:
        return None

    resource_type, resource_id = parsed
    spotify_name = _fetch_spotify_name(resource_type, resource_id)
    if spotify_name is None:
        return None

    return compute_similarity(release_title, spotify_name)
