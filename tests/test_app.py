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


class TestScrape:
    @patch("app.verify_spotify_link", return_value=1.0)
    @patch("app.get_latest_nmm_post")
    def test_scrape_renders_tables(self, mock_get_post, mock_verify, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        response = client.post("/scrape")
        assert response.status_code == 200
        assert b"Dancefloor" in response.data
        assert b"Liquid" in response.data
        assert b"Hoax, Breach" in response.data
        assert b"Jack (Hoax Rework)" in response.data
        assert b"Hospital" in response.data

    @patch("app.verify_spotify_link", return_value=1.0)
    @patch("app.get_latest_nmm_post")
    def test_scrape_renders_links(self, mock_get_post, mock_verify, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        response = client.post("/scrape")
        assert b"Beatport" in response.data
        assert b"Spotify" in response.data
        assert b"Bandcamp" in response.data

    @patch("app.verify_spotify_link", return_value=1.0)
    @patch("app.get_latest_nmm_post")
    def test_scrape_spotify_green_on_exact_match(self, mock_get_post, mock_verify, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        response = client.post("/scrape")
        assert b"btn-success" in response.data

    @patch("app.verify_spotify_link", return_value=0.8)
    @patch("app.get_latest_nmm_post")
    def test_scrape_spotify_orange_on_partial_match(self, mock_get_post, mock_verify, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        response = client.post("/scrape")
        assert b"btn-warning" in response.data

    @patch("app.verify_spotify_link", return_value=0.3)
    @patch("app.get_latest_nmm_post")
    def test_scrape_spotify_red_on_low_match(self, mock_get_post, mock_verify, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        response = client.post("/scrape")
        assert b"btn-danger" in response.data

    @patch("app.get_latest_nmm_post")
    def test_scrape_handles_error(self, mock_get_post, client):
        mock_get_post.side_effect = ValueError("No post found")
        response = client.post("/scrape")
        assert response.status_code == 200
        assert b"No post found" in response.data
