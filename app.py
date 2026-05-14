import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context

from beatport_client import scrape_beatport_track_names, verify_beatport_link
from beatport_playlist import (
    add_tracks_to_playlist as beatport_add_tracks_to_playlist,
    create_playlist as beatport_create_playlist,
    exchange_code as beatport_exchange_code,
    get_authorize_url as beatport_get_authorize_url,
    get_my_playlists,
    get_track_ids,
    is_authenticated as beatport_is_authenticated,
)
from parser import parse_releases
from reddit_client import get_latest_nmm_post
from spotify_client import compute_similarity, search_spotify, verify_spotify_link
from spotify_playlist import (
    add_tracks_to_playlist as spotify_add_tracks_to_playlist,
    create_playlist as spotify_create_playlist,
    exchange_code as spotify_exchange_code,
    get_authorize_url as spotify_get_authorize_url,
    is_authenticated as spotify_is_authenticated,
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
            release.spotify_match, release.spotify_title = result

    beatport_url = release.links.get("Beatport")
    if beatport_url:
        result = verify_beatport_link(release.title, beatport_url)
        if result is not None:
            release.beatport_match, release.beatport_title = result

    if not spotify_url:
        found, rejected = _search_spotify_cascade(release.artists, release.title, beatport_url)
        if found:
            release.links["Spotify"] = found["url"]
            release.spotify_match = found["match"]
            release.spotify_title = found["fetched_title"]
            release.spotify_auto = True
        elif rejected:
            release.spotify_search_rejected = rejected

    return release


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scrape")
def scrape():
    def generate():
        try:
            yield _sse("status", "Fetching latest New Music Monday post...")

            html = get_latest_nmm_post()
            sections = parse_releases(html)

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

    match, fetched_title = result
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
            best["source"] = "track_search"
            best["service"] = "Spotify"
            return best, best_rejected
        _track_rejected(best)

    # Step 3: if beatport URL provided, scrape first track name and retry
    if beatport_url:
        track_names = scrape_beatport_track_names(beatport_url)
        if track_names:
            track_query = f"{artist} {track_names[0]}".strip()

            results = search_spotify(track_query, "album")
            if results:
                best = _best_match(results, artist, title)
                if best and best["match"] >= threshold:
                    best["source"] = "beatport_track_album_search"
                    best["service"] = "Spotify"
                    return best, best_rejected
                _track_rejected(best)

            results = search_spotify(track_query, "track")
            if results:
                best = _best_match(results, artist, title)
                if best and best["match"] >= threshold:
                    if best.get("album_url"):
                        best["url"] = best["album_url"]
                        best["fetched_title"] = best.get("album_name", best["fetched_title"])
                    best["source"] = "beatport_track_search"
                    best["service"] = "Spotify"
                    return best, best_rejected
                _track_rejected(best)

    return None, best_rejected


@app.route("/spotify/search", methods=["POST"])
def spotify_search():
    data = request.get_json()
    artist = data.get("artist", "")
    title = data.get("title", "")
    beatport_url = data.get("beatport_url", "")
    if not title:
        return jsonify({"error": "title is required"}), 400

    result, rejected = _search_spotify_cascade(artist, title, beatport_url)
    if result:
        return jsonify(result)
    resp = {"service": "Spotify", "match": None, "error": "No good match found on Spotify"}
    if rejected:
        resp["best_rejected"] = rejected
    return jsonify(resp)


@app.route("/beatport/playlists")
def beatport_playlists():
    try:
        playlists = get_my_playlists()
        return jsonify(playlists)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/beatport/resolve-tracks", methods=["POST"])
def beatport_resolve_tracks():
    data = request.get_json()
    beatport_url = data.get("beatport_url", "")
    try:
        track_ids = get_track_ids(beatport_url)
        return jsonify({"track_ids": track_ids})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/beatport/add-tracks", methods=["POST"])
def beatport_add_tracks():
    data = request.get_json()
    playlist_id = data.get("playlist_id")
    track_ids = data.get("track_ids", [])
    if not playlist_id or not track_ids:
        return jsonify({"error": "playlist_id and track_ids are required"}), 400
    try:
        result = beatport_add_tracks_to_playlist(int(playlist_id), track_ids)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route("/beatport/authorize-url")
def beatport_authorize_url():
    """Return the Beatport OAuth authorize URL for the popup flow."""
    url = beatport_get_authorize_url()
    return jsonify({"url": url})


@app.route("/beatport/exchange", methods=["POST"])
def beatport_exchange():
    """Exchange a Beatport auth code (from postMessage popup) for tokens."""
    data = request.get_json()
    code = data.get("code")
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400
    try:
        beatport_exchange_code(code)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/create-playlists", methods=["POST"])
def create_playlists():
    """Create per-subgenre playlists on Beatport and Spotify."""
    data = request.get_json()
    prefix = data.get("prefix", "NMM")
    sections = data.get("sections", [])

    results = []

    for section in sections:
        section_name = section.get("name", "Unknown")
        playlist_name = f"{prefix} {section_name}"
        releases = section.get("releases", [])
        section_result = {"section": section_name, "playlist_name": playlist_name,
                          "beatport": None, "spotify": None}

        # Beatport
        try:
            if not beatport_is_authenticated():
                section_result["beatport"] = {"success": False, "error": "Not authenticated"}
            else:
                bp_track_ids = []
                for rel in releases:
                    beatport_url = rel.get("beatport_url", "")
                    if beatport_url:
                        ids = get_track_ids(beatport_url)
                        bp_track_ids.extend(ids)
                if bp_track_ids:
                    bp_playlist = beatport_create_playlist(playlist_name)
                    beatport_add_tracks_to_playlist(bp_playlist["id"], bp_track_ids)
                    section_result["beatport"] = {
                        "success": True,
                        "tracks_added": len(bp_track_ids),
                    }
                else:
                    section_result["beatport"] = {"success": True, "tracks_added": 0}
        except Exception as e:
            section_result["beatport"] = {"success": False, "error": str(e)}

        # Spotify
        try:
            if not spotify_is_authenticated():
                section_result["spotify"] = {"success": False, "error": "Not authenticated"}
            else:
                sp_uris = []
                for rel in releases:
                    spotify_url = rel.get("spotify_url", "")
                    if spotify_url:
                        uris = spotify_resolve_track_uris(spotify_url)
                        sp_uris.extend(uris)
                if sp_uris:
                    sp_playlist = spotify_create_playlist(playlist_name)
                    spotify_add_tracks_to_playlist(sp_playlist["id"], sp_uris)
                    section_result["spotify"] = {
                        "success": True,
                        "tracks_added": len(sp_uris),
                        "playlist_url": sp_playlist.get("url", ""),
                    }
                else:
                    section_result["spotify"] = {"success": True, "tracks_added": 0}
        except Exception as e:
            section_result["spotify"] = {"success": False, "error": str(e)}

        results.append(section_result)

    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True)
