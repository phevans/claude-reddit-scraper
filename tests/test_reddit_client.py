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

        other_post = MagicMock()
        other_post.title = "Some other post"

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
        newer_post.selftext_html = "<p>newer</p>"

        older_post = MagicMock()
        older_post.title = "New Music Monday! (Week of March 24)"
        older_post.selftext_html = "<p>older</p>"

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [newer_post, older_post]
        mock_reddit.redditor.return_value = mock_user

        result = get_latest_nmm_post()
        assert result == "<p>newer</p>"

    @patch.dict("os.environ", FAKE_ENV)
    @patch("reddit_client.praw.Reddit")
    def test_raises_when_no_matching_post(self, mock_reddit_class):
        mock_reddit = MagicMock()
        mock_reddit_class.return_value = mock_reddit

        post = MagicMock()
        post.title = "Something else entirely"

        mock_user = MagicMock()
        mock_user.submissions.new.return_value = [post]
        mock_reddit.redditor.return_value = mock_user

        with pytest.raises(ValueError, match="No 'New Music Monday!' post found"):
            get_latest_nmm_post()
