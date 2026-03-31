from flask import Flask, render_template

from parser import parse_releases
from reddit_client import get_latest_nmm_post
from spotify_client import verify_spotify_link

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", sections=None, error=None)


@app.route("/scrape", methods=["POST"])
def scrape():
    try:
        html = get_latest_nmm_post()
        sections = parse_releases(html)
        for section in sections:
            for release in section.releases:
                spotify_url = release.links.get("Spotify")
                if spotify_url:
                    release.spotify_match = verify_spotify_link(
                        release.title, spotify_url
                    )
        return render_template("index.html", sections=sections, error=None)
    except Exception as e:
        return render_template("index.html", sections=None, error=str(e))


if __name__ == "__main__":
    app.run(debug=True)
