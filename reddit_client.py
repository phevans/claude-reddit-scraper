import os

import praw
from dotenv import load_dotenv

load_dotenv()

_POSTER = "TELMxWILSON"


def _is_roundup(html: str) -> bool:
    """Whether a submission body is the weekly new-music roundup.

    Deliberately body-based, not title-based. The poster publishes a
    same-title *crosspost* (empty body) moments after the real post; its
    newer timestamp put it first in submissions.new(), so the old
    title-only match returned the empty crosspost and the parser found
    zero headings. Keying on the "New Releases" section the parser needs
    skips that crosspost and also survives title rebrands.
    """
    return "New Releases" in (html or "")


def get_latest_nmm_post() -> str:
    """Fetch the most recent weekly new-music roundup by TELMxWILSON and
    return its HTML body — the one that actually has a "New Releases"
    section, not the empty same-title crosspost.
    """
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "dnb-scraper/1.0"),
    )

    user = reddit.redditor(_POSTER)
    for submission in user.submissions.new(limit=50):
        # The real roundup is always the self-post to r/DnB; the poster
        # crossposts it (same title, empty body) moments later. Skip
        # crossposts outright, then confirm via the body.
        if getattr(submission, "crosspost_parent", None):
            continue
        html = submission.selftext_html or ""
        if _is_roundup(html):
            return html

    raise ValueError(
        "No weekly roundup post (with a 'New Releases' section) found "
        "in recent submissions"
    )
