"""One-time helper: obtain a Spotify refresh token via a loopback OAuth.

Spotify refresh tokens can only come from an interactive login. On the
serverless (Lambda) deploy there's no durable disk, so we capture the
refresh token once here and set it as the SPOTIFY_REFRESH_TOKEN env var;
the app then re-bootstraps auth from it on every cold start.

Prereqs:
  * SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET set in the environment.
  * http://127.0.0.1:8765/callback added to the app's Redirect URIs in
    the Spotify developer dashboard.

Run:  python scripts/get_spotify_refresh_token.py
It opens a browser, you approve, and it prints the refresh token.
"""
import http.server
import sys
import urllib.parse
import webbrowser

import spotify_playlist

REDIRECT_URI = "http://127.0.0.1:8765/callback"
_HOST, _PORT = "127.0.0.1", 8765
_captured: dict = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = params.get("code", [None])[0]
        self.send_response(200 if code else 400)
        self.end_headers()
        self.wfile.write(b"Done - you can close this tab and return to the terminal.")
        if code:
            _captured["code"] = code

    def log_message(self, *_):  # silence the default request logging
        pass


def main() -> int:
    if not spotify_playlist._CLIENT_ID or not spotify_playlist._CLIENT_SECRET:
        print("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET first.", file=sys.stderr)
        return 1

    auth_url = spotify_playlist.get_authorize_url(REDIRECT_URI)
    print("Opening browser to authorize Spotify...")
    print(auth_url)
    webbrowser.open(auth_url)

    server = http.server.HTTPServer((_HOST, _PORT), _Handler)
    while "code" not in _captured:
        server.handle_request()

    token = spotify_playlist.exchange_code(_captured["code"], REDIRECT_URI)
    refresh = token.get("refresh_token", "")
    if not refresh:
        print("No refresh_token in the response.", file=sys.stderr)
        return 1
    print("\nSPOTIFY_REFRESH_TOKEN=" + refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
