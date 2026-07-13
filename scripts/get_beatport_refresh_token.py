"""One-time helper: mint a fresh Beatport refresh token from username +
password (headless — no browser), for the BEATPORT_REFRESH_TOKEN env var.

Unlike Spotify, Beatport can authorize headlessly (see
beatport_playlist.login_with_password), so this needs no loopback server.
On the serverless (Lambda) deploy /tmp is wiped on cold start, so the app
re-bootstraps Beatport auth from BEATPORT_REFRESH_TOKEN every cold start.
When that token dies (expiry or rotation) every Beatport feature silently
fails — link verification, the step-3 VA cascade, and playlist writes —
because is_authenticated() only checks a token *exists*, not that it works.

Prereqs (keep the password OUT of your shell history / the transcript —
prefer a leading space or a secrets manager):
  * BEATPORT_USERNAME / BEATPORT_PASSWORD set in the environment.

Run:  BEATPORT_USERNAME=you BEATPORT_PASSWORD=... \
        python scripts/get_beatport_refresh_token.py

It prints the new refresh token AND probes whether Beatport rotates
refresh tokens on use (which decides whether a static env var can survive
cold starts, or whether we need durable token storage instead).
"""
import os
import sys

# Allow running as `python scripts/get_beatport_refresh_token.py` from the
# repo root: put the repo root (this file's parent dir) on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import beatport_playlist


def main() -> int:
    username = os.environ.get("BEATPORT_USERNAME")
    password = os.environ.get("BEATPORT_PASSWORD")
    if not username or not password:
        print(
            "Set BEATPORT_USERNAME and BEATPORT_PASSWORD in the environment "
            "first (prefix the command with a space to keep it out of shell "
            "history).",
            file=sys.stderr,
        )
        return 1

    print("Minting a fresh Beatport token via headless login...")
    token = beatport_playlist.login_with_password(username, password)
    refresh = token.get("refresh_token")
    if not refresh:
        print(f"No refresh_token in response: {sorted(token)}", file=sys.stderr)
        return 1

    print("\n=== NEW BEATPORT_REFRESH_TOKEN ===")
    print(refresh)
    print("==================================\n")

    # Rotation probe: refresh once and see whether Beatport hands back a
    # DIFFERENT refresh token. If it does, the token we just printed is
    # already spent — a static env var can't survive Lambda cold starts and
    # the token must be persisted durably (e.g. SSM) on every refresh.
    try:
        refreshed = beatport_playlist._refresh_access_token(refresh)
        rotated = refreshed.get("refresh_token")
        if rotated and rotated != refresh:
            print(
                "ROTATION DETECTED: Beatport issued a NEW refresh token on "
                "use, so the one above is now spent. Use this newest one, and "
                "note the env-var approach is NOT durable on scale-to-zero "
                "Lambda — needs persistent token storage:\n"
                f"{rotated}"
            )
        else:
            print(
                "No rotation: the refresh token is reusable, so setting it as "
                "BEATPORT_REFRESH_TOKEN is durable until it expires."
            )
    except Exception as e:  # noqa: BLE001 — diagnostic only
        print(f"(rotation probe failed: {type(e).__name__}: {e})")

    print(
        "\nNext: set this as the Lambda env var, e.g.\n"
        "  aws lambda update-function-configuration --function-name "
        "dnb-scraper --region eu-west-2 \\\n"
        "    --environment \"Variables={...,BEATPORT_REFRESH_TOKEN=<token>}\"\n"
        "(merge with existing vars — don't drop the others)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
