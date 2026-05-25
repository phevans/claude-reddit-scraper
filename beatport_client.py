"""Verify a Beatport URL against an expected release title.

Backed entirely by the authenticated /v4 API: Cloudflare blocks any
server-side scraping of beatport.com, so we never touch the HTML.
"""
from __future__ import annotations

from beatport_playlist import (
    extract_release_id,
    extract_track_id,
    get_release,
    get_release_tracks,
    get_track,
)
from spotify_client import compute_similarity


def verify_beatport_link(release_title: str, beatport_url: str) -> tuple[float, str, int] | None:
    """Check a Beatport URL against a release title.

    Handles both /release/<slug>/<id> and /track/<slug>/<id> URLs.
    Returns (similarity_score, beatport_title, track_count) or None when
    the URL isn't recognised, the API call fails, or we're not
    authenticated.
    """
    track_id = extract_track_id(beatport_url)
    if track_id is not None:
        track = get_track(track_id)
        if not track:
            return None
        name = (track.get("name") or "").strip()
        mix = (track.get("mix_name") or "").strip()
        beatport_title = f"{name} ({mix})" if mix and mix.lower() != "original mix" else name
        if not beatport_title:
            return None
        return compute_similarity(release_title, beatport_title), beatport_title, 1

    release_id = extract_release_id(beatport_url)
    if release_id is None:
        return None

    release = get_release(release_id)
    if not release:
        return None
    beatport_title = (release.get("name") or "").strip()
    if not beatport_title:
        return None

    # Prefer the release's own track_count field; if it's missing, fall
    # back to fetching the tracks list.
    track_count = release.get("track_count")
    if track_count is None:
        track_count = len(get_release_tracks(beatport_url))

    return compute_similarity(release_title, beatport_title), beatport_title, track_count
