from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from spotify_client import compute_similarity

_BEATPORT_URL_PATTERN = re.compile(r"beatport\.com/release/[^/]+/\d+")


def _parse_title_from_og(soup: BeautifulSoup) -> str | None:
    og_title = soup.find("meta", property="og:title")
    if not og_title:
        return None
    content = og_title.get("content", "")
    title_part = content.split(" | Music")[0].strip()
    dash_idx = title_part.find(" - ")
    bracket_idx = title_part.rfind(" [")
    if dash_idx != -1 and bracket_idx != -1 and bracket_idx > dash_idx:
        return title_part[dash_idx + 3:bracket_idx].strip()
    elif dash_idx != -1:
        return title_part[dash_idx + 3:].strip()
    return title_part


def _parse_tracks_from_soup(soup: BeautifulSoup) -> list[str]:
    tracks = []
    for span in soup.select("span.buk-track-primary-title"):
        name = span.get_text(strip=True)
        if name:
            tracks.append(name)
    if tracks:
        return tracks
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            ld = json.loads(script.string or "")
            if isinstance(ld, dict) and "track" in ld:
                for t in ld["track"]:
                    name = t.get("name", "")
                    if name:
                        tracks.append(name)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return tracks


def _fetch_beatport_page(url: str) -> tuple[str | None, list[str]]:
    """Fetch a Beatport release page once, returning (title, track_names)."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; dnb-scraper/1.0)"},
            timeout=10,
        )
        if response.status_code != 200:
            return None, []
    except requests.RequestException:
        return None, []

    soup = BeautifulSoup(response.text, "html.parser")
    return _parse_title_from_og(soup), _parse_tracks_from_soup(soup)


def _fetch_beatport_title(url: str) -> str | None:
    title, _ = _fetch_beatport_page(url)
    return title


def scrape_beatport_track_names(url: str) -> list[str]:
    """Scrape track names from a Beatport release page."""
    _, tracks = _fetch_beatport_page(url)
    return tracks


def verify_beatport_link(release_title: str, beatport_url: str) -> tuple[float, str, int] | None:
    """Check a Beatport URL against a release title.

    Returns (similarity_score, beatport_title, track_count) or None on failure.
    """
    if not _BEATPORT_URL_PATTERN.search(beatport_url):
        return None

    beatport_title, tracks = _fetch_beatport_page(beatport_url)
    if beatport_title is None:
        return None

    return compute_similarity(release_title, beatport_title), beatport_title, len(tracks)
