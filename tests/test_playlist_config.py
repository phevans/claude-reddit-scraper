from playlist_config import (
    CANONICAL_KEYS,
    SECTION_SIGNATURES,
    SPOTIFY_PERSISTENT_PLAYLISTS,
    SPOTIFY_YEARLY_PLAYLISTS,
    classify_section,
    lookup_spotify_playlists,
)


class TestConfigShape:
    def test_persistent_and_yearly_share_canonical_keys(self):
        assert set(SPOTIFY_PERSISTENT_PLAYLISTS) == set(CANONICAL_KEYS)
        assert set(SPOTIFY_YEARLY_PLAYLISTS) == set(CANONICAL_KEYS)

    def test_signature_table_covers_every_canonical_key(self):
        # If a canonical bucket lacks a signature, classify_section can
        # never route to it — silent dead config.
        assert set(SECTION_SIGNATURES) == set(CANONICAL_KEYS)


class TestClassifyExactHeadings:
    """The 7 original Reddit headings should classify deterministically."""

    def test_original_headings(self):
        cases = {
            "General DnB / Mixed": "general",
            "Dancefloor": "dancefloor",
            "Liquid": "liquid",
            "Deep / Tech / Minimal": "deep_tech_min",
            "Neuro": "neuro",
            "Jump Up": "jump_up",
            "Jungle / Halftime / Experimental": "jungle_etc",
        }
        for heading, expected in cases.items():
            assert classify_section(heading) == expected, \
                f"'{heading}' should classify as '{expected}'"


class TestClassifyDriftedHeadings:
    """The point of the classifier: tolerate wording drift."""

    def test_extra_words_still_match(self):
        assert classify_section("Liquid Vibez") == "liquid"
        assert classify_section("Liquid Funk Special") == "liquid"
        assert classify_section("Dancefloor Heat") == "dancefloor"

    def test_neurofunk_prefix_matches_neuro(self):
        # 'neuro' is a prefix of 'neurofunk', so the signature catches
        # it without us having to list every -funk / -bass variant.
        assert classify_section("Neurofunk Special") == "neuro"
        assert classify_section("Neurobass") == "neuro"

    def test_reordering_and_punctuation(self):
        assert classify_section("Deep & Minimal Tech") == "deep_tech_min"
        assert classify_section("Tech / Minimal / Deep") == "deep_tech_min"
        assert classify_section("Jungle, Halftime & Experimental") == "jungle_etc"

    def test_case_and_whitespace_insensitive(self):
        assert classify_section("  LIQUID  ") == "liquid"
        assert classify_section("jUmP   uP") == "jump_up"

    def test_count_beats_precision(self):
        # 'Deep Tech' matches deep_tech_min on 2 of 3 signature tokens
        # (precision 0.67) vs nothing else — clear winner.
        assert classify_section("Deep Tech") == "deep_tech_min"
        # 'Neuro Tech Minimal' matches deep_tech_min on 2 tokens,
        # neuro on 1. Count wins.
        assert classify_section("Neuro Tech Minimal") == "deep_tech_min"


class TestClassifyAmbiguousAndUnknown:
    def test_unknown_returns_none(self):
        assert classify_section("Hardcore") is None
        assert classify_section("Drumfunk") is None
        assert classify_section("") is None
        assert classify_section(None) is None

    def test_ambiguous_returns_none(self):
        # 'Dancefloor & Jump Up' matches both dancefloor and jump_up
        # with 1 token each, precision 1.0 each — refuse to pick.
        assert classify_section("Dancefloor & Jump Up") is None
        # Same shape: Liquid + Neuro both 1.0.
        assert classify_section("Liquid Neuro") is None

    def test_generic_dnb_alone_is_unknown(self):
        # 'DnB' is deliberately excluded from the 'general' signature
        # because every section is in a DnB context. Reddit using just
        # 'DnB' as a heading should fall through to unmapped.
        assert classify_section("DnB") is None


class TestLookupSpotifyPlaylists:
    def test_returns_urls_via_classifier(self):
        result = lookup_spotify_playlists("Liquid")
        assert result is not None
        assert result["persistent"] == SPOTIFY_PERSISTENT_PLAYLISTS["liquid"]
        assert result["yearly"] == SPOTIFY_YEARLY_PLAYLISTS["liquid"]
        assert result["canonical_key"] == "liquid"

    def test_drifted_name_still_resolves(self):
        result = lookup_spotify_playlists("Neurofunk Special")
        assert result is not None
        assert result["canonical_key"] == "neuro"

    def test_unknown_returns_none(self):
        assert lookup_spotify_playlists("Hardcore") is None
