from unittest.mock import patch

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestIndex:
    def test_get_index_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_get_index_contains_run_button(self, client):
        response = client.get("/")
        assert b"Run" in response.data
        assert b"startScrape" in response.data


class TestScrape:
    def _collect_events(self, response):
        """Parse SSE events from a streaming response."""
        events = []
        current_event = None
        data_lines = []
        for line in response.data.decode().split("\n"):
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
            elif line == "" and current_event is not None:
                events.append((current_event, "\n".join(data_lines)))
                current_event = None
                data_lines = []
        return events

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone"))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_streams_sections(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        response = client.get("/scrape")
        assert response.status_code == 200
        assert "text/event-stream" in response.content_type

        events = self._collect_events(response)
        event_types = [e[0] for e in events]

        assert "status" in event_types
        assert "progress" in event_types
        assert "section" in event_types
        assert "done" in event_types

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone"))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_streams_both_subgenres(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_events = [data for evt, data in events if evt == "section"]

        assert len(section_events) == 2
        assert "Dancefloor" in section_events[0]
        assert "Liquid" in section_events[1]

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone"))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_section_contains_release_data(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_events = [data for evt, data in events if evt == "section"]

        assert "Hoax, Breach" in section_events[0]
        assert "Jack (Hoax Rework)" in section_events[0]
        assert "Hospital" in section_events[0]

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone"))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_green_on_exact_match(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_html = " ".join(data for evt, data in events if evt == "section")
        assert "link-match-exact" in section_html

    @patch("app.verify_beatport_link", return_value=(0.8, "Wrong Beatport Title"))
    @patch("app.verify_spotify_link", return_value=(0.8, "Wrong Spotify Title"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_orange_on_partial_match(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_html = " ".join(data for evt, data in events if evt == "section")
        assert "link-match-partial" in section_html
        assert "0.80" in section_html
        assert "Wrong Spotify Title" in section_html
        assert "Wrong Beatport Title" in section_html

    @patch("app.verify_beatport_link", return_value=(0.3, "Totally Wrong"))
    @patch("app.verify_spotify_link", return_value=(0.3, "Completely Different"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_red_on_low_match(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_html = " ".join(data for evt, data in events if evt == "section")
        assert "link-match-poor" in section_html
        assert "0.30" in section_html
        assert "Completely Different" in section_html
        assert "Totally Wrong" in section_html

    @patch("app.get_latest_nmm_post")
    def test_scrape_streams_error(self, mock_get_post, client):
        mock_get_post.side_effect = ValueError("No post found")
        events = self._collect_events(client.get("/scrape"))
        error_events = [data for evt, data in events if evt == "error"]
        assert len(error_events) == 1
        assert "No post found" in error_events[0]
