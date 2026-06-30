"""One-time helper: obtain a Spotify refresh token via a loopback OAuth.

Spotify refresh tokens can only come from an interactive login. On the
serverless (Lambda) deploy there's no durable disk, so we capture the
refresh token once here and set it as the SPOTIFY_REFRESH_TOKEN env var;
the app then re-bootstraps auth from it on every cold start.

Uses an HTTPS loopback: Spotify validates plain-http loopback URIs but
then returns server_error at code issuance, so we serve the callback over
TLS with a throwaway self-signed cert (the browser will warn once on the
127.0.0.1 redirect — click through it).

Prereqs:
  * SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET set in the environment.
  * https://127.0.0.1:8765/callback added to the app's Redirect URIs in
    the Spotify developer dashboard.

Run:  python scripts/get_spotify_refresh_token.py
It opens a browser, you approve, and it prints the refresh token.
"""
import http.server
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import webbrowser

# Allow running as `python scripts/get_spotify_refresh_token.py` from the
# repo root: put the repo root (this file's parent dir) on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spotify_playlist

REDIRECT_URI = "https://127.0.0.1:8765/callback"
_HOST, _PORT = "127.0.0.1", 8765
_captured: dict = {}


def _self_signed_cert() -> tuple[str, str]:
    """Generate a throwaway self-signed cert/key for 127.0.0.1 via openssl.
    Returns (cert_path, key_path)."""
    d = tempfile.mkdtemp(prefix="spotify_oauth_")
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", cert, "-days", "1",
         "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return cert, key


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        self.send_response(200 if code else 400)
        self.end_headers()
        self.wfile.write(b"Done - you can close this tab and return to the terminal.")
        if code:
            _captured["code"] = code
        elif error:
            _captured["error"] = error

    def log_message(self, *_):  # silence the default request logging
        pass


def main() -> int:
    if not spotify_playlist._CLIENT_ID or not spotify_playlist._CLIENT_SECRET:
        print("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET first.", file=sys.stderr)
        return 1

    auth_url = spotify_playlist.get_authorize_url(REDIRECT_URI)
    print("Open this URL in your browser and approve:\n")
    print("  " + auth_url + "\n")
    webbrowser.open(auth_url)

    server = http.server.HTTPServer((_HOST, _PORT), _Handler)
    cert, key = _self_signed_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    while not _captured:
        server.handle_request()

    if _captured.get("error"):
        print(f"\nSpotify returned an error: {_captured['error']}", file=sys.stderr)
        print("This is often transient — just run the script again. If it "
              "persists, confirm the app isn't in a restricted state and that "
              "127.0.0.1:8765/callback is saved as a Redirect URI.", file=sys.stderr)
        return 2

    token = spotify_playlist.exchange_code(_captured["code"], REDIRECT_URI)
    refresh = token.get("refresh_token", "")
    if not refresh:
        print("No refresh_token in the response.", file=sys.stderr)
        return 1
    print("\nSPOTIFY_REFRESH_TOKEN=" + refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
