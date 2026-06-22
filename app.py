import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from version import VERSION

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context

from beatport_client import verify_beatport_link
from beatport_playlist import (
    add_tracks_to_playlist as beatport_add_tracks_to_playlist,
    create_playlist as beatport_create_playlist,
    get_release_tracks as beatport_get_release_tracks,
    get_track_ids,
    is_authenticated as beatport_is_authenticated,
    login_with_password as beatport_login_with_password,
    strip_mix_name,
)
from parser import parse_releases
from playlist_config import (
    CANONICAL_DISPLAY_NAMES,
    CANONICAL_KEYS,
    classify_section,
    lookup_spotify_playlists,
)
from reddit_client import get_latest_nmm_post
from spotify_client import compute_similarity, search_spotify, verify_spotify_link
from spotify_playlist import (
    add_tracks_to_playlist as spotify_add_tracks_to_playlist,
    create_playlist as spotify_create_playlist,
    delete_playlist as spotify_delete_playlist,
    exchange_code as spotify_exchange_code,
    extract_playlist_id as spotify_extract_playlist_id,
    find_user_playlists_by_name_prefix as spotify_find_playlists_by_prefix,
    get_authorize_url as spotify_get_authorize_url,
    get_playlist as spotify_get_playlist,
    get_playlist_track_uris as spotify_get_playlist_track_uris,
    get_tracks_info as spotify_get_tracks_info,
    is_authenticated as spotify_is_authenticated,
    replace_playlist_tracks as spotify_replace_playlist_tracks,
    resolve_track_uris as spotify_resolve_track_uris,
)

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

MAX_WORKERS = 10
PREFERRED_SERVICE_ORDER = ["Beatport", "Bandcamp", "Spotify"]


def _verify_release(release):
    """Verify a single release against Spotify and Beatport in sequence."""
    spotify_url = release.links.get("Spotify")
    if spotify_url:
        result = verify_spotify_link(release.title, spotify_url)
        if result is not None:
            release.spotify_match, release.spotify_title, release.spotify_artists = result

    beatport_url = release.links.get("Beatport")
    if beatport_url:
        result = verify_beatport_link(release.title, beatport_url)
        if result is not None:
            release.beatport_match, release.beatport_title, release.beatport_track_count = result

    if not spotify_url:
        found, rejected = _search_spotify_cascade(release.artists, release.title, beatport_url)
        # Step-3 (beatport-track) sources score against the *track*, not
        # the release title, so a same-track-different-album false
        # positive can sail through. Refuse to auto-apply when the
        # album-title similarity is too low; surface as a "rejected"
        # candidate so the user can still see it via the search button.
        if found and _passes_album_sanity(found):
            release.links["Spotify"] = found["url"]
            release.spotify_match = found["match"]
            release.spotify_title = found["fetched_title"]
            release.spotify_artists = found.get("artists") or None
            release.spotify_auto = True
        elif found:
            release.spotify_search_rejected = found
        elif rejected:
            release.spotify_search_rejected = rejected

    return release


# Below this, the candidate's album title is judged "completely
# different" from the Reddit release title — we warn the user and
# refuse to auto-apply.
ALBUM_SANITY_THRESHOLD = 0.4


def _passes_album_sanity(found: dict) -> bool:
    score = found.get("album_title_match")
    if score is None:
        return True
    return score >= ALBUM_SANITY_THRESHOLD


def _missing_canonical_sections(sections) -> list[str]:
    """Return canonical keys (in CANONICAL_KEYS order) that no parsed
    section classifies into. Used to surface "post seems to be missing
    Liquid" warnings.
    """
    hit: set[str] = set()
    for section in sections:
        key = classify_section(section.name)
        if key:
            hit.add(key)
    return [k for k in CANONICAL_KEYS if k not in hit]


@app.route("/")
def index():
    return render_template("index.html", version=VERSION)


@app.route("/scrape")
def scrape():
    def generate():
        try:
            yield _sse("status", "Fetching latest New Music Monday post...")

            if not beatport_is_authenticated():
                yield _sse(
                    "warning",
                    "Beatport isn't connected — release titles, track counts, and the Spotify cascade "
                    "(which looks up the first Beatport track to recover from VA releases) will be skipped. "
                    "Connect Beatport above to enable.",
                )

            html = get_latest_nmm_post()
            sections = parse_releases(html)

            # Sanity check: every canonical subgenre bucket should be
            # represented in the post. If one isn't, either the post
            # genuinely omitted it (rare) or the parser/classifier
            # missed a renamed heading — surface it so we don't
            # silently drop a section's tracks.
            missing = _missing_canonical_sections(sections)
            if missing:
                labels = ", ".join(CANONICAL_DISPLAY_NAMES[k] for k in missing)
                yield _sse(
                    "warning",
                    f"Missing expected section(s) in this post: {labels}. "
                    "Either the post omitted them, or the heading text drifted enough that "
                    "the classifier didn't route it to a canonical bucket. Check the post.",
                )

            # Collect all unique services across all sections
            all_services_set = {"Spotify"}
            for section in sections:
                for release in section.releases:
                    all_services_set.update(release.links.keys())

            # Order: preferred services first, then any others alphabetically
            extra = sorted(all_services_set - set(PREFERRED_SERVICE_ORDER))
            all_services = [s for s in PREFERRED_SERVICE_ORDER if s in all_services_set] + extra

            total_releases = sum(len(s.releases) for s in sections)
            completed = 0

            yield _sse("progress", {"completed": 0, "total": total_releases})

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                for section in sections:
                    futures = {
                        executor.submit(_verify_release, release): release
                        for release in section.releases
                    }
                    for future in as_completed(futures):
                        future.result()
                        completed += 1
                        yield _sse("progress", {"completed": completed, "total": total_releases})

                    section_html = render_template(
                        "section_table.html", section=section, all_services=all_services
                    )
                    yield _sse("section", section_html)

            yield _sse("done", "")

        except Exception as e:
            yield _sse("error", str(e))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data) -> str:
    if isinstance(data, dict):
        data = json.dumps(data)
    # SSE requires newlines in data to be prefixed with "data: "
    lines = str(data).split("\n")
    payload = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{payload}\n\n"


@app.route("/verify-link", methods=["POST"])
def verify_link():
    data = request.get_json()
    url = data.get("url", "")
    release_title = data.get("release_title", "")
    if not url or not release_title:
        return jsonify({"error": "url and release_title are required"}), 400

    if "spotify.com" in url:
        result = verify_spotify_link(release_title, url)
        service = "Spotify"
    elif "beatport.com" in url:
        result = verify_beatport_link(release_title, url)
        service = "Beatport"
    else:
        return jsonify({"service": "Unknown", "match": None, "fetched_title": None})

    if result is None:
        return jsonify({"service": service, "match": None, "fetched_title": None, "error": "Could not fetch title from URL"})

    # Beatport returns (score, title, track_count); Spotify returns (score, title, artists)
    match = result[0]
    fetched_title = result[1]
    return jsonify({"service": service, "match": round(match, 4), "fetched_title": fetched_title})


def _best_match(results, release_artist, release_title):
    """Find the best matching result from a Spotify search, comparing artist+title."""
    release_combined = f"{release_artist} - {release_title}".strip(" -")
    best = None
    for r in results:
        result_combined = f"{r.get('artists', '')} - {r['name']}".strip(" -")
        score = compute_similarity(release_combined, result_combined)
        if best is None or score > best["match"]:
            best = {"match": round(score, 4), "fetched_title": r["name"], "url": r.get("url", ""), "artists": r.get("artists", "")}
            if "album_url" in r:
                best["album_url"] = r["album_url"]
                best["album_name"] = r.get("album_name", "")
    return best


def _all_matches(results, release_artist, release_title):
    """Score every result and return them all sorted by score descending."""
    release_combined = f"{release_artist} - {release_title}".strip(" -")
    scored = []
    for r in results:
        result_combined = f"{r.get('artists', '')} - {r['name']}".strip(" -")
        score = compute_similarity(release_combined, result_combined)
        entry = {"match": round(score, 4), "fetched_title": r["name"], "url": r.get("url", ""), "artists": r.get("artists", "")}
        if "album_url" in r:
            entry["album_url"] = r["album_url"]
            entry["album_name"] = r.get("album_name", "")
        scored.append(entry)
    scored.sort(key=lambda x: x["match"], reverse=True)
    return scored


def _search_spotify_cascade(artist, title, beatport_url="", threshold=0.6):
    """Run the cascading Spotify search.

    Returns (result_dict, best_rejected) where best_rejected is the
    highest-scoring match that fell below threshold, or None.
    """
    query = f"{artist} {title}".strip()
    best_rejected = None

    def _track_rejected(candidate):
        nonlocal best_rejected
        if candidate and (best_rejected is None or candidate["match"] > best_rejected["match"]):
            best_rejected = dict(candidate)

    # Step 1: album search by artist + title
    results = search_spotify(query, "album")
    if results:
        best = _best_match(results, artist, title)
        if best and best["match"] >= threshold:
            best["source"] = "album_search"
            best["service"] = "Spotify"
            return best, best_rejected
        _track_rejected(best)

    # Step 2: track search by artist + title
    results = search_spotify(query, "track")
    if results:
        best = _best_match(results, artist, title)
        if best and best["match"] >= threshold:
            if best.get("album_url"):
                best["url"] = best["album_url"]
                best["fetched_title"] = best.get("album_name", best["fetched_title"])
                if best.get("album_artists"):
                    best["artists"] = best["album_artists"]
            best["source"] = "track_search"
            best["service"] = "Spotify"
            return best, best_rejected
        _track_rejected(best)

    # Step 3: if beatport URL provided, look up first track and retry. For
    # compilations the release "artist" is generic ("Various Artists",
    # "VA") and confuses Spotify, so use the first track's actual artist
    # when one is available.
    if beatport_url:
        tracks = _beatport_first_tracks(beatport_url)
        if tracks:
            first = tracks[0]
            search_artist = artist
            if _is_various_artists(artist) and first.get("artists"):
                search_artist = first["artists"]
            track_query = f"{search_artist} {first['name']}".strip()
            # Score candidates against the first track's real artist+title
            # too — otherwise a "Various Artists" release will always
            # score badly against the actual track artist.
            score_against_artist = search_artist
            score_against_title = first["name"]

            results = search_spotify(track_query, "album")
            if results:
                best = _best_match(results, score_against_artist, score_against_title)
                if best and best["match"] >= threshold:
                    # In an album search the candidate's fetched_title
                    # IS the album title — score it against the Reddit
                    # release title to catch unrelated-album hits.
                    best["album_title_match"] = round(
                        compute_similarity(title, best["fetched_title"]), 4
                    )
                    best["source"] = "beatport_track_album_search"
                    best["service"] = "Spotify"
                    return best, best_rejected
                _track_rejected(best)

            results = search_spotify(track_query, "track")
            if results:
                best = _best_match(results, score_against_artist, score_against_title)
                if best and best["match"] >= threshold:
                    if best.get("album_url"):
                        best["url"] = best["album_url"]
                        best["fetched_title"] = best.get("album_name", best["fetched_title"])
                        if best.get("album_artists"):
                            best["artists"] = best["album_artists"]
                    # Sanity-check the resolved album title against the
                    # Reddit release title (see beatport_track_album_search
                    # branch above).
                    best["album_title_match"] = round(
                        compute_similarity(title, best["fetched_title"]), 4
                    )
                    best["source"] = "beatport_track_search"
                    best["service"] = "Spotify"
                    return best, best_rejected
                _track_rejected(best)

    return None, best_rejected


def _collect_cascade_candidates(artist: str, title: str, beatport_url: str = "", n: int = 5) -> list[dict]:
    """Run all cascade steps and return the top-N candidates without early-returning.

    Steps 1 and 2 contribute their single best match. Step 3 contributes all
    scored results from each sub-search so that multiple albums sharing the
    same first track all appear as candidates.
    """
    query = f"{artist} {title}".strip()
    seen_urls: set[str] = set()
    candidates: list[dict] = []

    def _add(candidate, source):
        if not candidate:
            return
        c = dict(candidate)
        if c.get("album_url"):
            c["url"] = c["album_url"]
            c["fetched_title"] = c.get("album_name") or c["fetched_title"]
            if c.get("album_artists"):
                c["artists"] = c["album_artists"]
        url = c.get("url", "")
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        c["source"] = source
        c["service"] = "Spotify"
        c.pop("album_url", None)
        c.pop("album_name", None)
        c.pop("album_artists", None)
        candidates.append(c)

    results = search_spotify(query, "album")
    if results:
        _add(_best_match(results, artist, title), "album_search")

    results = search_spotify(query, "track")
    if results:
        _add(_best_match(results, artist, title), "track_search")

    if beatport_url:
        tracks = _beatport_first_tracks(beatport_url)
        if tracks:
            first = tracks[0]
            search_artist = artist
            if _is_various_artists(artist) and first.get("artists"):
                search_artist = first["artists"]
            track_query = f"{search_artist} {first['name']}".strip()
            sa, st = search_artist, first["name"]

            # Use limit=10 and add every result so albums sharing the same
            # first track all surface as candidates rather than the top-1 only.
            results = search_spotify(track_query, "album", limit=10)
            if results:
                for r in _all_matches(results, sa, st):
                    _add(r, "beatport_album_search")

            results = search_spotify(track_query, "track", limit=10)
            if results:
                for r in _all_matches(results, sa, st):
                    _add(r, "beatport_track_search")

    candidates.sort(key=lambda c: c["match"], reverse=True)
    return candidates[:n]


def _search_spotify_raw_candidates(query: str, score_artist: str, score_title: str, n: int = 3) -> list[dict]:
    """Search Spotify with a raw query, scoring results against the original artist+title."""
    release_combined = f"{score_artist} - {score_title}".strip(" -")
    seen_urls: set[str] = set()
    candidates: list[dict] = []

    for search_type in ("album", "track"):
        results = search_spotify(query, search_type)
        if not results:
            continue
        for r in results:
            url = r.get("album_url") or r.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            fetched_title = r.get("album_name") or r.get("name", "")
            artists = r.get("album_artists") or r.get("artists", "") if r.get("album_url") else r.get("artists", "")
            result_combined = f"{artists} - {fetched_title}".strip(" -")
            score = compute_similarity(release_combined, result_combined)
            candidates.append({
                "match": round(score, 4),
                "fetched_title": fetched_title,
                "url": url,
                "artists": artists,
                "source": f"custom_{search_type}_search",
                "service": "Spotify",
            })

    candidates.sort(key=lambda c: c["match"], reverse=True)
    return candidates[:n]


def _is_various_artists(artist: str) -> bool:
    a = (artist or "").strip().lower()
    return a in {"various artists", "various", "va", "v/a", "v.a."}


def _beatport_first_tracks(beatport_url: str) -> list[dict]:
    """Return tracks for a Beatport release as [{name, artists}, ...].

    Pulls from the authenticated /v4 API. The track 'name' is the bare
    title — the mix variant ('Original Mix', 'Extended Mix') is
    intentionally dropped because it pollutes Spotify searches.
    """
    out = []
    for t in beatport_get_release_tracks(beatport_url):
        name = strip_mix_name((t.get("name") or "").strip())
        if not name:
            continue
        artists = ", ".join(
            a.get("name", "") for a in t.get("artists", []) if isinstance(a, dict)
        )
        out.append({"name": name, "artists": artists})
    return out


@app.route("/spotify/search", methods=["POST"])
def spotify_search():
    data = request.get_json()
    artist = data.get("artist", "")
    title = data.get("title", "")
    beatport_url = data.get("beatport_url", "")
    query = (data.get("query") or "").strip()
    if not title and not query:
        return jsonify({"error": "title or query is required"}), 400

    if query:
        candidates = _search_spotify_raw_candidates(query, artist, title)
    else:
        candidates = _collect_cascade_candidates(artist, title, beatport_url)
    return jsonify({"candidates": candidates})


def _get_callback_uri(path):
    """Build a callback URI, respecting CloudFront/proxy HTTPS."""
    proto = request.headers.get("CloudFront-Forwarded-Proto",
                                request.headers.get("X-Forwarded-Proto",
                                                    request.scheme))
    host = request.headers.get("Host", request.host)
    return f"{proto}://{host}{path}"


@app.route("/auth-status")
def auth_status():
    return jsonify({
        "spotify": spotify_is_authenticated(),
        "beatport": beatport_is_authenticated(),
    })


@app.route("/spotify/authorize-url")
def spotify_authorize_url():
    """Return the Spotify OAuth authorize URL for the popup flow."""
    redirect_uri = _get_callback_uri("/spotify/callback")
    url = spotify_get_authorize_url(redirect_uri)
    return jsonify({"url": url})


@app.route("/spotify/callback")
def spotify_callback():
    code = request.args.get("code")
    error = request.args.get("error")
    if error:
        return f"<html><body><p>Spotify authorization failed: {error}</p></body></html>", 400
    if not code:
        return "<html><body><p>Missing authorization code</p></body></html>", 400
    redirect_uri = _get_callback_uri("/spotify/callback")
    spotify_exchange_code(code, redirect_uri)
    # Close the popup and notify the opener
    return """<html><body><script>
        if (window.opener) {
            window.opener.postMessage({service: 'spotify', success: true}, '*');
        }
        window.close();
    </script><p>Spotify connected! You can close this window.</p></body></html>"""


@app.route("/beatport/login", methods=["POST"])
def beatport_login():
    """Log in to Beatport with username + password (server-side OAuth)."""
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400
    try:
        beatport_login_with_password(username, password)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_section_result(prefix: str, section: dict) -> dict:
    """Create playlists for one section on both services. Long-running
    per call (many sequential HTTP requests per release), so the
    endpoint streams these one at a time via SSE."""
    section_name = section.get("name", "Unknown")
    playlist_name = f"{prefix} {section_name}"
    releases = section.get("releases", [])
    section_result = {"section": section_name, "playlist_name": playlist_name,
                      "beatport": None, "spotify": None}

    if section.get("skip_beatport"):
        section_result["beatport"] = None
    else:
        try:
            if not beatport_is_authenticated():
                section_result["beatport"] = {"success": False, "error": "Not authenticated"}
            else:
                bp_track_ids = []
                skipped = 0
                for rel in releases:
                    beatport_url = rel.get("beatport_url", "")
                    if not beatport_url:
                        continue
                    # get_track_ids resolves a bad/stale URL to [] rather
                    # than raising, so one broken link can't fail the
                    # whole section — just count it as skipped.
                    ids = get_track_ids(beatport_url)
                    if ids:
                        bp_track_ids.extend(ids)
                    else:
                        skipped += 1
                if bp_track_ids:
                    bp_playlist = beatport_create_playlist(playlist_name)
                    beatport_add_tracks_to_playlist(bp_playlist["id"], bp_track_ids)
                    section_result["beatport"] = {
                        "success": True, "tracks_added": len(bp_track_ids), "skipped": skipped
                    }
                else:
                    section_result["beatport"] = {
                        "success": True, "tracks_added": 0, "skipped": skipped
                    }
        except Exception as e:
            section_result["beatport"] = {"success": False, "error": str(e)}

    if section.get("skip_spotify"):
        section_result["spotify"] = None
    else:
        try:
            if not spotify_is_authenticated():
                section_result["spotify"] = {"success": False, "error": "Not authenticated"}
            else:
                sp_uris = []
                for rel in releases:
                    spotify_url = rel.get("spotify_url", "")
                    if spotify_url:
                        sp_uris.extend(spotify_resolve_track_uris(spotify_url))
                section_result["spotify"] = _spotify_section_update(
                    section_name, playlist_name, sp_uris,
                )
        except Exception as e:
            section_result["spotify"] = {"success": False, "error": str(e)}

    return section_result


def _spotify_section_update(section_name: str, fallback_name: str,
                            new_uris: list[str]) -> dict:
    """Apply the persistent + backup + yearly write for one section.

    Order of operations (chosen so there's always at least one backup
    of the previous contents on disk at every moment):

      1. Resolve persistent + yearly URLs from the section name.
      2. If no new_uris were resolved, do NOTHING destructive — leave
         the persistent playlist alone. (A run that finds zero Spotify
         links for a section shouldn't wipe last week's data.)
      3. Read persistent's current name and track URIs.
      4. If old tracks exist, create a fresh backup playlist named
         "<persistent name> backup <YYYY-MM-DD>" containing them.
      5. Replace persistent with new_uris.
      6. Append new_uris to the yearly playlist (no dedup — user's
         explicit choice; duplicates surface re-issues / reposts).
      7. Delete any OTHER playlist whose name starts with
         "<persistent name> backup " — so only the freshly-made
         backup survives.

    For sections with no persistent mapping the caller's old behaviour
    is retained: a standalone playlist is created and `unmapped: True`
    is set so the UI can flag it.
    """
    if not new_uris:
        return {"success": True, "tracks_added": 0,
                "note": "No Spotify tracks found in this section."}

    mapping = lookup_spotify_playlists(section_name)
    if not mapping:
        # Unmapped subgenre — fall back to one-off creation, flag it.
        sp_playlist = spotify_create_playlist(fallback_name)
        spotify_add_tracks_to_playlist(sp_playlist["id"], new_uris)
        return {
            "success": True,
            "tracks_added": len(new_uris),
            "playlist_url": sp_playlist.get("url", ""),
            "unmapped": True,
        }

    persistent_id = spotify_extract_playlist_id(mapping["persistent"])
    if not persistent_id:
        return {"success": False,
                "error": f"Could not parse persistent playlist URL: {mapping['persistent']}"}

    persistent_meta = spotify_get_playlist(persistent_id)
    persistent_name = persistent_meta.get("name") or fallback_name
    backup_prefix = f"{persistent_name} backup "

    old_uris = spotify_get_playlist_track_uris(persistent_id)

    backup_url = None
    new_backup_id = None
    if old_uris:
        backup = spotify_create_playlist(
            f"{backup_prefix}{date.today().isoformat()}"
        )
        spotify_add_tracks_to_playlist(backup["id"], old_uris)
        backup_url = backup.get("url", "")
        new_backup_id = backup["id"]

    spotify_replace_playlist_tracks(persistent_id, new_uris)

    yearly_added = 0
    if mapping.get("yearly"):
        yearly_id = spotify_extract_playlist_id(mapping["yearly"])
        if yearly_id:
            spotify_add_tracks_to_playlist(yearly_id, new_uris)
            yearly_added = len(new_uris)

    # Sweep any older backups for this section (do this LAST so we never
    # have a window with zero backups).
    deleted_old_backups = 0
    for p in spotify_find_playlists_by_prefix(backup_prefix):
        if p["id"] == new_backup_id:
            continue
        try:
            spotify_delete_playlist(p["id"])
            deleted_old_backups += 1
        except Exception:
            # Don't fail the whole section over a stale backup we couldn't
            # unfollow — surface it instead.
            pass

    return {
        "success": True,
        "tracks_added": len(new_uris),
        "playlist_url": mapping["persistent"],
        "backup_url": backup_url,
        "yearly_added": yearly_added,
        "old_backups_deleted": deleted_old_backups,
        "previous_track_count": len(old_uris),
    }


def _build_section_plan(prefix: str, section: dict) -> dict:
    """Read-only counterpart to _build_section_result. Returns what
    WOULD happen on commit, without touching any playlist.
    """
    section_name = section.get("name", "Unknown")
    playlist_name = f"{prefix} {section_name}"
    releases = section.get("releases", [])
    plan = {"section": section_name, "playlist_name": playlist_name,
            "beatport": None, "spotify": None}

    # Beatport plan is intentionally thin until the persistent-Beatport
    # flow lands. For now it's "we would create a new playlist with N
    # tracks" — same as today's commit behaviour.
    try:
        if not beatport_is_authenticated():
            plan["beatport"] = {"success": False, "error": "Not authenticated"}
        else:
            bp_track_ids = []
            for rel in releases:
                beatport_url = rel.get("beatport_url", "")
                if beatport_url:
                    bp_track_ids.extend(get_track_ids(beatport_url))
            plan["beatport"] = {
                "success": True,
                "would_create": playlist_name,
                "track_count": len(bp_track_ids),
            }
    except Exception as e:
        plan["beatport"] = {"success": False, "error": str(e)}

    try:
        if not spotify_is_authenticated():
            plan["spotify"] = {"success": False, "error": "Not authenticated"}
        else:
            sp_uris = []
            for rel in releases:
                spotify_url = rel.get("spotify_url", "")
                if spotify_url:
                    sp_uris.extend(spotify_resolve_track_uris(spotify_url))
            plan["spotify"] = _spotify_section_plan(
                section_name, playlist_name, sp_uris,
            )
    except Exception as e:
        plan["spotify"] = {"success": False, "error": str(e)}

    return plan


def _spotify_section_plan(section_name: str, fallback_name: str,
                          new_uris: list[str]) -> dict:
    """Diff the proposed new_uris against the persistent playlist's
    current contents, without modifying anything. Mirrors the shape of
    _spotify_section_update's return value so the UI can render the
    same fields.

    Track-level fields (`to_add`, `to_remove`) include name + artists
    via spotify_get_tracks_info so the diff is reviewable at a glance.
    Tracks Spotify can't resolve come back with name=None and the UI
    renders them as "<unknown — spotify:track:xxxxx>".
    """
    if not new_uris:
        return {"success": True, "tracks_added": 0,
                "note": "No Spotify tracks found in this section."}

    mapping = lookup_spotify_playlists(section_name)
    if not mapping:
        # Unmapped: would create a standalone playlist.
        return {
            "success": True,
            "tracks_added": len(new_uris),
            "unmapped": True,
            "fallback_name": fallback_name,
            "to_add": spotify_get_tracks_info(new_uris),
            "to_remove": [],
        }

    persistent_id = spotify_extract_playlist_id(mapping["persistent"])
    if not persistent_id:
        return {"success": False,
                "error": f"Could not parse persistent playlist URL: {mapping['persistent']}"}

    persistent_meta = spotify_get_playlist(persistent_id)
    persistent_name = persistent_meta.get("name") or fallback_name
    backup_prefix = f"{persistent_name} backup "

    old_uris = spotify_get_playlist_track_uris(persistent_id)
    old_set = set(old_uris)
    new_set = set(new_uris)

    to_add_uris = [u for u in new_uris if u not in old_set]
    to_remove_uris = [u for u in old_uris if u not in new_set]
    unchanged_count = len(old_set & new_set)

    # The double-click footgun: if the new set exactly matches the
    # current persistent set, the commit would back up "this week's"
    # data and replace it with the same data — losing last week's
    # backup. Flag it so the UI can render a no-op warning.
    no_op = (not to_add_uris and not to_remove_uris)

    existing_backups = spotify_find_playlists_by_prefix(backup_prefix)
    backup = None
    if old_uris and not no_op:
        # Note: we don't actually know the date that would be used at
        # commit time (it'd be commit's "today"), so we don't put one
        # in the planned name. The UI can describe it as "a new
        # backup playlist".
        backup = {
            "track_count": len(old_uris),
            "replaces": [p["name"] for p in existing_backups],
        }

    yearly_add_count = 0
    if mapping.get("yearly") and not no_op:
        yearly_id = spotify_extract_playlist_id(mapping["yearly"])
        if yearly_id:
            yearly_add_count = len(new_uris)

    # Resolve names for the human review.
    to_add = spotify_get_tracks_info(to_add_uris)
    to_remove = spotify_get_tracks_info(to_remove_uris)

    return {
        "success": True,
        "tracks_added": len(new_uris),
        "playlist_url": mapping["persistent"],
        "persistent_name": persistent_name,
        "current_track_count": len(old_uris),
        "new_track_count": len(new_uris),
        "unchanged_count": unchanged_count,
        "to_add": to_add,
        "to_remove": to_remove,
        "backup": backup,
        "yearly_add_count": yearly_add_count,
        "no_op": no_op,
    }


@app.route("/preview-playlists", methods=["POST"])
def preview_playlists():
    """Stream per-section diff previews via SSE.

    Identical event protocol to /create-playlists so the frontend's
    SSE plumbing is reused. The preview is purely informative — no
    destructive Spotify calls are made here, only reads.
    """
    data = request.get_json()
    prefix = data.get("prefix", "NMM")
    sections = data.get("sections", [])

    def generate():
        try:
            yield _sse("total", {"total": len(sections)})
            for section in sections:
                plan = _build_section_plan(prefix, section)
                yield _sse("section", plan)
            yield _sse("done", "")
        except Exception as e:
            yield _sse("error", str(e))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/create-playlists", methods=["POST"])
def create_playlists():
    """Stream per-section playlist results via SSE.

    The previous JSON version timed out at CloudFront / gunicorn (~30s)
    for any non-trivial scrape, which delivered an HTML error page that
    the client tried to JSON-parse.
    """
    data = request.get_json()
    prefix = data.get("prefix", "NMM")
    sections = data.get("sections", [])

    def generate():
        try:
            yield _sse("total", {"total": len(sections)})
            for section in sections:
                result = _build_section_result(prefix, section)
                yield _sse("section", result)
            yield _sse("done", "")
        except Exception as e:
            yield _sse("error", str(e))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True)
