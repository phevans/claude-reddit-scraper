from parser import parse_releases


class TestParseReleases:
    def test_returns_correct_number_of_subgenres(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        assert len(sections) == 2

    def test_subgenre_names(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        assert sections[0].name == "Dancefloor"
        assert sections[1].name == "Liquid"

    def test_correct_number_of_releases_per_subgenre(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        assert len(sections[0].releases) == 3
        assert len(sections[1].releases) == 2

    def test_single_link_release(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        release = sections[0].releases[0]  # Acelin, Maddy Lucas
        assert release.artists == "Acelin, Maddy Lucas"
        assert release.title == "Home Alone"
        assert release.label == "YosH"
        assert len(release.links) == 1
        assert "Beatport" in release.links

    def test_multiple_links_release(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        release = sections[0].releases[1]  # Hoax, Breach
        assert release.artists == "Hoax, Breach"
        assert release.title == "Jack (Hoax Rework)"
        assert release.label == "Hospital"
        assert len(release.links) == 3
        assert "Beatport" in release.links
        assert "Bandcamp" in release.links
        assert "Spotify" in release.links

    def test_ampersand_in_artist_name(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        release = sections[0].releases[2]  # Chase & Status
        assert release.artists == "Chase & Status, Pozer"
        assert release.title == "Through The Pain"
        assert release.label == "Warner"

    def test_link_urls_are_correct(self, sample_post_html):
        sections = parse_releases(sample_post_html)
        release = sections[0].releases[1]  # Hoax, Breach
        assert "beatport.com" in release.links["Beatport"]
        assert "bandcamp.com" in release.links["Bandcamp"]
        assert "spotify.com" in release.links["Spotify"]

    def test_spotify_only_release(self, sample_post_html):
        """Some releases have only Spotify (with a leading comma/space before it)."""
        sections = parse_releases(sample_post_html)
        release = sections[1].releases[1]  # Sebass
        assert release.artists == "Sebass"
        assert release.title == "our summerlove"
        assert release.label == "Selfreleased"
        assert "Spotify" in release.links

    def test_no_releases_section_returns_empty(self, sample_post_html_no_releases):
        sections = parse_releases(sample_post_html_no_releases)
        assert sections == []

    def test_empty_html_returns_empty(self):
        assert parse_releases("") == []
