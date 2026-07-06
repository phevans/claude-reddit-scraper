import os

import praw
from dotenv import load_dotenv

load_dotenv()

_POSTER = "TELMxWILSON"
_SUBREDDIT = "DnB"


def _iter_candidate_submissions(reddit):
    """Yield the poster's recent submissions, newest-first, deduped by id.

    Two independent sources, because Reddit's per-user submission listing
    (`/user/<name>/submitted`) has started returning an EMPTY listing for
    this poster (profile-visibility / API quirk we don't control) — which
    on its own hides the roundup entirely and raised the "no roundup
    found" error even though the post is live.

    Source 1 is that original user listing (kept in case it recovers).
    Source 2 is a subreddit author-search, which returns the poster's
    posts newest-first and reaches back several weeks regardless of how
    busy r/DnB is (scanning the subreddit's raw `.new` feed would miss a
    several-day-old weekly post behind a day of traffic). Each source is
    isolated so one failing or returning nothing doesn't sink the other.
    """
    seen = set()

    def _emit(source):
        for submission in source:
            sid = getattr(submission, "id", None)
            if sid is not None and sid in seen:
                continue
            if sid is not None:
                seen.add(sid)
            yield submission

    try:
        yield from _emit(reddit.redditor(_POSTER).submissions.new(limit=50))
    except Exception:
        pass
    try:
        yield from _emit(
            reddit.subreddit(_SUBREDDIT).search(
                f"author:{_POSTER}", sort="new", limit=25
            )
        )
    except Exception:
        pass


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

    for submission in _iter_candidate_submissions(reddit):
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
