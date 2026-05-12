import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from beatport_client import verify_beatport_link
from beatport_playlist import add_tracks_to_playlist, get_my_playlists, get_track_ids
from parser import parse_releases
from reddit_client import get_latest_nmm_post
from spotify_client import verify_spotify_link

app = Flask(__name__)

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
            all_services_set = set()
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
        result = add_tracks_to_playlist(int(playlist_id), track_ids)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
