from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from spotify_client import compute_similarity

_BEATPORT_URL_PATTERN = re.compile(r"beatport\.com/release/[^/]+/\d+")


def _fetch_beatport_title(url: str) -> str | None:
    """Fetch the release title from a Beatport release page via its og:title meta tag."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; dnb-scraper/1.0)"},
            timeout=10,
        )
        if response.status_code != 200:
            return None
    except requests.RequestException:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    og_title = soup.find("meta", property="og:title")
    if not og_title:
        return None

    # Format: "Artist - Title [Label] | Music & Downloads on Beatport"
    content = og_title.get("content", "")
    # Strip the Beatport suffix
    title_part = content.split(" | Music")[0].strip()
    # Extract just the track/release title: between first " - " and last " ["
    dash_idx = title_part.find(" - ")
    bracket_idx = title_part.rfind(" [")
    if dash_idx != -1 and bracket_idx != -1 and bracket_idx > dash_idx:
        return title_part[dash_idx + 3:bracket_idx].strip()
    elif dash_idx != -1:
        return title_part[dash_idx + 3:].strip()
    return title_part


def verify_beatport_link(release_title: str, beatport_url: str) -> tuple[float, str] | None:
    """Check a Beatport URL against a release title.

    Returns (similarity_score, beatport_title) or None on failure.
    """
    if not _BEATPORT_URL_PATTERN.search(beatport_url):
        return None

    beatport_title = _fetch_beatport_title(beatport_url)
    if beatport_title is None:
        return None

    return compute_similarity(release_title, beatport_title), beatport_title
