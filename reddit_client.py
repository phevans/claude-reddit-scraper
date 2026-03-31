import os

import praw
from dotenv import load_dotenv

load_dotenv()


def get_latest_nmm_post() -> str:
    """Fetch the most recent 'New Music Monday!' post by TELMxWILSON and return its HTML body."""
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "dnb-scraper/1.0"),
    )

    user = reddit.redditor("TELMxWILSON")
    for submission in user.submissions.new(limit=50):
        if "New Music Monday!" in submission.title:
            return submission.selftext_html or ""

    raise ValueError("No 'New Music Monday!' post found in recent submissions")
