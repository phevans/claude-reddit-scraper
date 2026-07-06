from unittest.mock import MagicMock, patch

import pytest

from reddit_client import get_latest_nmm_post


FAKE_ENV = {
    "REDDIT_CLIENT_ID": "fake_id",
    "REDDIT_CLIENT_SECRET": "fake_secret",
    "REDDIT_USER_AGENT": "test-agent/1.0",
}


class TestGetLatestNmmPost:
    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_returns_html_of_matching_post(self, mock_reddit_class):
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        matching_post = MagicMock()
        matching_post.title = "New Music Monday! (Week of March 31, 2026)"
        matching_post.selftext_html = "<h1>New Releases</h1>"
        matching_post.crosspost_parent = None

        other_post = MagicMock()
        other_post.title = "Some other post"
        other_post.selftext_html = "<p>no releases here</p>"
        other_post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [other_post, matching_post]
        mock_reddit.redditor.return_value = mock_user

        result = get_latest_nmm_post()
        assert result == "<h1>New Releases</h1>"
        mock_reddit.redditor.assert_called_once_with("TELMxWILSON")

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_returns_first_matching_post(self, mock_reddit_class):
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        newer_post = MagicMock()
        newer_post.title = "New Music Monday! (Week of March 31)"
        newer_post.selftext_html = "<h1>New Releases</h1><p>newer</p>"
        newer_post.crosspost_parent = None

        older_post = MagicMock()
        older_post.title = "New Music Monday! (Week of March 24)"
        older_post.selftext_html = "<h1>New Releases</h1><p>older</p>"
        older_post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [newer_post, older_post]
        mock_reddit.redditor.return_value = mock_user

        result = get_latest_nmm_post()
        assert result == "<h1>New Releases</h1><p>newer</p>"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_skips_empty_crosspost_with_same_title(self, mock_reddit_class):
        # The actual production bug: the poster crossposts the roundup
        # (same title, empty body) seconds after the real self-post, so
        # the crosspost sorts first in submissions.new(). We must skip it
        # and return the real post that has the "New Releases" section.
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        crosspost = MagicMock()
        crosspost.title = "Fresh music! ... | New Music Monday! (Week 26)"
        crosspost.selftext_html = ""  # crossposts carry no body
        crosspost.crosspost_parent = "t3_1uiqr6q"

        real_post = MagicMock()
        real_post.title = "Fresh music! ... | New Music Monday! (Week 26)"
        real_post.selftext_html = "<h1>New Releases</h1><h3>Dancefloor</h3>"
        real_post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [crosspost, real_post]
        mock_reddit.redditor.return_value = mock_user

        assert get_latest_nmm_post() == "<h1>New Releases</h1><h3>Dancefloor</h3>"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_matches_fresh_music_rebrand(self, mock_reddit_class):
        # The poster rebranded the title from "New Music Monday!" to
        # "Fresh Music: ..."; the old exact-match broke on this. Regression.
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        post = MagicMock()
        post.title = "Fresh Music: New Tunes From Alix Perez, Hedex & more"
        post.selftext_html = "<h1>New Releases</h1>"
        post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [post]
        mock_reddit.redditor.return_value = mock_user

        assert get_latest_nmm_post() == "<h1>New Releases</h1>"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_matches_by_body_when_title_unrecognised(self, mock_reddit_class):
        # Future-proofing: even an unknown title is matched if the body
        # carries the "New Releases" section the parser needs.
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        post = MagicMock()
        post.title = "Totally Rebranded Weekly Thread"
        post.selftext_html = "<h1>New Releases</h1><h3>Liquid</h3>"
        post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [post]
        mock_reddit.redditor.return_value = mock_user

        assert get_latest_nmm_post() == "<h1>New Releases</h1><h3>Liquid</h3>"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_falls_back_to_subreddit_search_when_user_listing_empty(
        self, mock_reddit_class
    ):
        # Production regression: Reddit started returning an EMPTY
        # /user/<name>/submitted listing for the poster, so the roundup
        # was invisible. The subreddit author-search must still find it.
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = []  # the outage
        mock_reddit.redditor.return_value = mock_user

        real_post = MagicMock()
        real_post.title = "Fresh Music! New tunes ... | New Music Monday! (Week 27)"
        real_post.selftext_html = "<h1>New Releases</h1><h3>Dancefloor</h3>"
        real_post.crosspost_parent = None

        mock_subreddit = MagicMock()
        mock_subreddit.search.return_value = [real_post]
        mock_reddit.subreddit.return_value = mock_subreddit

        assert get_latest_nmm_post() == "<h1>New Releases</h1><h3>Dancefloor</h3>"
        mock_reddit.subreddit.assert_called_once_with("DnB")
        assert mock_subreddit.search.call_args.args[0] == "author:TELMxWILSON"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_deduplicates_post_present_in_both_sources(self, mock_reddit_class):
        # The same submission can appear in both the user listing and the
        # subreddit search; it must not be yielded (or processed) twice.
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        post = MagicMock()
        post.id = "1uov8fk"
        post.selftext_html = "<h1>New Releases</h1>"
        post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [post]
        mock_reddit.redditor.return_value = mock_user

        mock_subreddit = MagicMock()
        mock_subreddit.search.return_value = [post]
        mock_reddit.subreddit.return_value = mock_subreddit

        assert get_latest_nmm_post() == "<h1>New Releases</h1>"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_raises_when_no_matching_post(self, mock_reddit_class):
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        post = MagicMock()
        post.title = "Something else entirely"
        post.selftext_html = "<p>just a chat post</p>"
        post.crosspost_parent = None

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [post]
        mock_reddit.redditor.return_value = mock_user

        with pytest.raises(ValueError, match="No weekly roundup post"):
            get_latest_nmm_post()
