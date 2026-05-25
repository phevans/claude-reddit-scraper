from unittest.mock import MagicMock, patch

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

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone", 0))
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

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone", 0))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_streams_both_subgenres(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_events = [data for evt, data in events if evt == "section"]

        assert len(section_events) == 2
        assert "Dancefloor" in section_events[0]
        assert "Liquid" in section_events[1]

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone", 0))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_section_contains_release_data(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_events = [data for evt, data in events if evt == "section"]

        assert "Hoax, Breach" in section_events[0]
        assert "Jack (Hoax Rework)" in section_events[0]
        assert "Hospital" in section_events[0]

    @patch("app.verify_beatport_link", return_value=(1.0, "Home Alone", 0))
    @patch("app.verify_spotify_link", return_value=(1.0, "Home Alone"))
    @patch("app.get_latest_nmm_post")
    def test_scrape_green_on_exact_match(self, mock_get_post, mock_spotify, mock_beatport, client, sample_post_html):
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        section_html = " ".join(data for evt, data in events if evt == "section")
        assert "link-match-exact" in section_html

    @patch("app.verify_beatport_link", return_value=(0.8, "Wrong Beatport Title", 0))
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

    @patch("app.verify_beatport_link", return_value=(0.3, "Totally Wrong", 0))
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

    @patch("app.verify_beatport_link", return_value=None)
    @patch("app.verify_spotify_link", return_value=None)
    @patch("app.get_latest_nmm_post")
    def test_scrape_warns_on_missing_canonical_sections(
        self, mock_get_post, _sp, _bp, client, sample_post_html
    ):
        # The sample post has only Dancefloor + Liquid; the other 5
        # canonical buckets are missing and should be enumerated in
        # the warning event.
        mock_get_post.return_value = sample_post_html
        events = self._collect_events(client.get("/scrape"))
        warnings = [data for evt, data in events if evt == "warning"]
        # There's one (optional) "Beatport not connected" warning that
        # may appear too; isolate the missing-sections one by content.
        missing_warning = next(
            (w for w in warnings if "Missing expected section" in w), None,
        )
        assert missing_warning is not None
        for label in ("General DnB / Mixed", "Neuro", "Jump Up",
                      "Deep / Tech / Minimal",
                      "Jungle / Halftime / Experimental"):
            assert label in missing_warning


class TestSpotifySectionUpdate:
    """The persistent + backup + yearly write logic.

    Order matters here: we want the new backup to exist BEFORE old
    backups are unfollowed, and we want the persistent playlist
    untouched if there are no new tracks.
    """

    def _patch_helpers(self, *, persistent_name="NMM Liquid",
                       persistent_url="https://open.spotify.com/playlist/PERSIST",
                       persistent_id="PERSIST",
                       yearly_url="https://open.spotify.com/playlist/YEARLY",
                       yearly_id="YEARLY",
                       old_uris=None,
                       existing_backups=None):
        """Returns a context-manager stack as a list of patchers."""
        old_uris = old_uris if old_uris is not None else ["spotify:track:OLD1"]
        existing_backups = existing_backups if existing_backups is not None else []

        patches = [
            patch("app.lookup_spotify_playlists", return_value={
                "persistent": persistent_url, "yearly": yearly_url,
            }),
            patch("app.spotify_extract_playlist_id",
                  side_effect=lambda u: persistent_id if "PERSIST" in u else yearly_id),
            patch("app.spotify_get_playlist", return_value={
                "id": persistent_id, "name": persistent_name, "url": persistent_url,
            }),
            patch("app.spotify_get_playlist_track_uris", return_value=old_uris),
            patch("app.spotify_create_playlist", return_value={
                "id": "NEWBACKUP", "name": f"{persistent_name} backup 2026-05-25",
                "url": "https://open.spotify.com/playlist/NEWBACKUP",
            }),
            patch("app.spotify_add_tracks_to_playlist"),
            patch("app.spotify_replace_playlist_tracks"),
            patch("app.spotify_find_playlists_by_prefix",
                  return_value=existing_backups),
            patch("app.spotify_delete_playlist"),
        ]
        return patches

    def _enter(self, patches):
        return [p.start() for p in patches], patches

    def _exit(self, patches):
        for p in patches:
            p.stop()

    def test_no_new_tracks_leaves_persistent_alone(self):
        from app import _spotify_section_update
        patches = self._patch_helpers()
        mocks, _ = self._enter(patches)
        try:
            result = _spotify_section_update("Liquid", "NMM Liquid", [])
        finally:
            self._exit(patches)
        # Critical: no destructive call when new_uris is empty.
        for m in mocks:
            if hasattr(m, "assert_not_called") and m._mock_name in (
                "spotify_replace_playlist_tracks", "spotify_create_playlist",
                "spotify_delete_playlist", "spotify_add_tracks_to_playlist",
            ):
                m.assert_not_called()
        assert result["tracks_added"] == 0
        assert result["success"] is True

    def test_full_flow_backs_up_then_replaces_then_yearly_then_deletes(self):
        from app import _spotify_section_update
        existing_backups = [
            {"id": "OLDBACKUP1", "name": "NMM Liquid backup 2026-05-18"},
            {"id": "OLDBACKUP2", "name": "NMM Liquid backup 2026-05-11"},
        ]
        patches = self._patch_helpers(existing_backups=existing_backups)
        mocks, _ = self._enter(patches)
        try:
            new = ["spotify:track:NEW1", "spotify:track:NEW2"]
            result = _spotify_section_update("Liquid", "NMM Liquid", new)
        finally:
            self._exit(patches)

        # Backup creation: a new playlist was made with the dated suffix.
        create_call = next(m for m in mocks if m._mock_name == "spotify_create_playlist")
        create_call.assert_called_once()
        assert "backup" in create_call.call_args[0][0]

        # Old tracks went into the new backup.
        add_calls = next(m for m in mocks if m._mock_name == "spotify_add_tracks_to_playlist")
        # First add: old uris -> new backup. Second add: new uris -> yearly.
        assert add_calls.call_count == 2
        assert add_calls.call_args_list[0][0] == ("NEWBACKUP", ["spotify:track:OLD1"])
        assert add_calls.call_args_list[1][0] == ("YEARLY", new)

        # Persistent replaced with the new set.
        replace = next(m for m in mocks if m._mock_name == "spotify_replace_playlist_tracks")
        replace.assert_called_once_with("PERSIST", new)

        # Old backups deleted; the new one was not.
        delete = next(m for m in mocks if m._mock_name == "spotify_delete_playlist")
        deleted_ids = [c.args[0] for c in delete.call_args_list]
        assert "NEWBACKUP" not in deleted_ids
        assert set(deleted_ids) == {"OLDBACKUP1", "OLDBACKUP2"}

        assert result["tracks_added"] == 2
        assert result["previous_track_count"] == 1
        assert result["yearly_added"] == 2
        assert result["old_backups_deleted"] == 2
        assert result["backup_url"] == "https://open.spotify.com/playlist/NEWBACKUP"

    def test_skips_backup_when_persistent_was_empty(self):
        from app import _spotify_section_update
        patches = self._patch_helpers(old_uris=[])
        mocks, _ = self._enter(patches)
        try:
            result = _spotify_section_update("Liquid", "NMM Liquid", ["spotify:track:NEW1"])
        finally:
            self._exit(patches)

        # No backup playlist created when there was nothing to back up.
        create_call = next(m for m in mocks if m._mock_name == "spotify_create_playlist")
        create_call.assert_not_called()
        # But the persistent still gets replaced with the new uris.
        replace = next(m for m in mocks if m._mock_name == "spotify_replace_playlist_tracks")
        replace.assert_called_once_with("PERSIST", ["spotify:track:NEW1"])

        assert result["backup_url"] is None
        assert result["previous_track_count"] == 0

    def test_unmapped_section_falls_back_to_one_off(self):
        from app import _spotify_section_update
        with patch("app.lookup_spotify_playlists", return_value=None), \
             patch("app.spotify_create_playlist", return_value={
                 "id": "ONEOFF", "name": "NMM Hardcore",
                 "url": "https://open.spotify.com/playlist/ONEOFF",
             }) as create_mock, \
             patch("app.spotify_add_tracks_to_playlist") as add_mock:
            result = _spotify_section_update("Hardcore", "NMM Hardcore",
                                              ["spotify:track:X"])

        create_mock.assert_called_once_with("NMM Hardcore")
        add_mock.assert_called_once_with("ONEOFF", ["spotify:track:X"])
        assert result["unmapped"] is True
        assert result["playlist_url"] == "https://open.spotify.com/playlist/ONEOFF"

    def test_old_backup_deletion_failure_does_not_break_run(self):
        from app import _spotify_section_update
        existing = [{"id": "OLDBACKUP", "name": "NMM Liquid backup 2026-05-18"}]
        patches = self._patch_helpers(existing_backups=existing)
        # Override delete to raise.
        for p in patches:
            if p.attribute == "spotify_delete_playlist":
                p.new = MagicMock(side_effect=RuntimeError("API exploded"))
        mocks, _ = self._enter(patches)
        try:
            result = _spotify_section_update("Liquid", "NMM Liquid",
                                              ["spotify:track:NEW"])
        finally:
            self._exit(patches)

        # The run still reports success — we don't fail the whole
        # section over a stale-backup cleanup error.
        assert result["success"] is True
        assert result["old_backups_deleted"] == 0


class TestSearchSpotifyCascade:
    """The 3-step cascade in _search_spotify_cascade:
       1. Album search by `artist title`.
       2. Track search by `artist title` (promoted to album if available).
       3. If a beatport_url is given: pull the first track and re-search,
          swapping in the track's actual artist for VA releases. Annotate
          the result with album_title_match so the caller can sanity-check.

    Tests mock search_spotify + _best_match so the cascade's branching
    is what's exercised, not the underlying similarity scoring (which
    has its own tests).
    """

    THRESHOLD_OK = 0.9
    THRESHOLD_FAIL = 0.4  # below the default 0.6 threshold

    def _best(self, match, **extra):
        base = {
            "match": match, "fetched_title": "Some Title",
            "url": "https://example/spotify", "artists": "An Artist",
        }
        base.update(extra)
        return base

    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step1_hit_returns_album_search(self, mock_search, mock_best):
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_best.return_value = self._best(self.THRESHOLD_OK)

        result, rejected = _search_spotify_cascade("Acelin", "Home Alone")

        assert result is not None
        assert result["source"] == "album_search"
        assert result["service"] == "Spotify"
        assert result["match"] == self.THRESHOLD_OK
        assert rejected is None
        # Step 2 + 3 should not have been reached.
        assert mock_search.call_count == 1

    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step2_promotes_track_to_album_when_available(self, mock_search, mock_best):
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        # Step 1 misses, step 2 hits with album_url present.
        mock_best.side_effect = [
            self._best(self.THRESHOLD_FAIL),  # step 1 miss
            self._best(self.THRESHOLD_OK, album_url="https://album/url",
                       album_name="The Album"),  # step 2 hit
        ]

        result, _ = _search_spotify_cascade("Acelin", "Home Alone")

        assert result["source"] == "track_search"
        assert result["url"] == "https://album/url"
        # The track-hit's fetched_title gets replaced with album_name
        # so the UI displays the album, not the individual track.
        assert result["fetched_title"] == "The Album"

    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step2_keeps_track_url_when_no_album_url(self, mock_search, mock_best):
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_best.side_effect = [
            self._best(self.THRESHOLD_FAIL),
            self._best(self.THRESHOLD_OK),  # no album_url
        ]
        result, _ = _search_spotify_cascade("A", "T")
        assert result["url"] == "https://example/spotify"
        assert result["source"] == "track_search"

    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_all_steps_miss_no_beatport_returns_none(self, mock_search, mock_best):
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_best.return_value = self._best(self.THRESHOLD_FAIL)

        result, rejected = _search_spotify_cascade("A", "T")

        assert result is None
        # The best sub-threshold candidate is still surfaced.
        assert rejected is not None
        assert rejected["match"] == self.THRESHOLD_FAIL

    @patch("app._beatport_first_tracks", return_value=[])
    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step3_skipped_when_beatport_returns_no_tracks(
        self, mock_search, mock_best, _tracks
    ):
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_best.return_value = self._best(self.THRESHOLD_FAIL)
        result, _ = _search_spotify_cascade("A", "T", beatport_url="https://bp/x/1")
        assert result is None
        # 2 search_spotify calls (steps 1+2), not 4 (steps 3a+3b skipped).
        assert mock_search.call_count == 2

    @patch("app._beatport_first_tracks")
    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step3_album_hit_annotates_album_title_match(
        self, mock_search, mock_best, mock_tracks
    ):
        """The defining behaviour of step 3: when we re-search using the
        Beatport track's identity, we must score the candidate's album
        title against the original Reddit release title. That secondary
        score is what stops us from auto-applying a same-track,
        different-album false positive (see TestAlbumSanityCheck).
        """
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_tracks.return_value = [{"name": "Gritty", "artists": "Milzee"}]
        mock_best.side_effect = [
            self._best(self.THRESHOLD_FAIL),  # step 1
            self._best(self.THRESHOLD_FAIL),  # step 2
            self._best(self.THRESHOLD_OK, fetched_title="Some Other Album"),  # step 3 album
        ]

        result, _ = _search_spotify_cascade(
            "Various Artists", "Pioneers Five EP",
            beatport_url="https://www.beatport.com/release/pioneers-five/1",
        )

        assert result["source"] == "beatport_track_album_search"
        # The annotation is real: it's the similarity between the Reddit
        # release title ("Pioneers Five EP") and the Spotify album title
        # ("Some Other Album"). Calculated with the real compute_similarity
        # — should be low.
        assert "album_title_match" in result
        assert result["album_title_match"] < 0.4

    @patch("app._beatport_first_tracks")
    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step3_swaps_in_track_artist_for_va_releases(
        self, mock_search, mock_best, mock_tracks
    ):
        """The VA fix: when the Reddit release artist is 'Various Artists',
        step 3 should use the first track's *actual* artist when querying
        and scoring. Without this, VA compilations always score
        catastrophically and never match.
        """
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_tracks.return_value = [{"name": "Gritty", "artists": "Milzee"}]
        mock_best.side_effect = [
            self._best(self.THRESHOLD_FAIL),
            self._best(self.THRESHOLD_FAIL),
            self._best(self.THRESHOLD_OK),  # step 3 album hit
        ]

        _search_spotify_cascade(
            "Various Artists", "Pioneers Five EP",
            beatport_url="https://www.beatport.com/release/x/1",
        )

        # The step-3 _best_match call should score against the TRACK
        # artist+title, not "Various Artists - Pioneers Five EP".
        step3_best_call = mock_best.call_args_list[2]
        _, score_artist, score_title = step3_best_call.args
        assert score_artist == "Milzee"
        assert score_title == "Gritty"

        # And the search query should also use the track artist.
        step3_search_call = mock_search.call_args_list[2]
        query, _kind = step3_search_call.args
        assert "Milzee" in query
        assert "Gritty" in query

    @patch("app._beatport_first_tracks")
    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step3_uses_reddit_artist_for_non_va_releases(
        self, mock_search, mock_best, mock_tracks
    ):
        """Conversely: for a normal (non-VA) release, step 3 should NOT
        substitute the track artist. The Reddit artist is canonical.
        """
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_tracks.return_value = [{"name": "Different", "artists": "Some Producer"}]
        mock_best.side_effect = [
            self._best(self.THRESHOLD_FAIL),
            self._best(self.THRESHOLD_FAIL),
            self._best(self.THRESHOLD_OK),
        ]
        _search_spotify_cascade("Acelin", "Home Alone",
                                 beatport_url="https://bp/x/1")
        step3_best_call = mock_best.call_args_list[2]
        _, score_artist, _ = step3_best_call.args
        # Crucially still "Acelin", not "Some Producer".
        assert score_artist == "Acelin"

    @patch("app._beatport_first_tracks")
    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_step3_falls_through_to_track_subsearch_if_album_misses(
        self, mock_search, mock_best, mock_tracks
    ):
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_tracks.return_value = [{"name": "Gritty", "artists": "Milzee"}]
        mock_best.side_effect = [
            self._best(self.THRESHOLD_FAIL),  # step 1
            self._best(self.THRESHOLD_FAIL),  # step 2
            self._best(self.THRESHOLD_FAIL),  # step 3 album
            self._best(self.THRESHOLD_OK,
                       album_url="https://album/url", album_name="Found Album"),
        ]
        result, _ = _search_spotify_cascade(
            "Various Artists", "Pioneers Five EP",
            beatport_url="https://bp/x/1",
        )
        assert result["source"] == "beatport_track_search"
        # Promoted to album.
        assert result["url"] == "https://album/url"
        assert result["fetched_title"] == "Found Album"
        # And still annotated with album_title_match.
        assert "album_title_match" in result

    @patch("app._best_match")
    @patch("app.search_spotify")
    def test_best_rejected_tracks_highest_subthreshold_across_steps(
        self, mock_search, mock_best
    ):
        """When all steps miss, best_rejected should be the highest-scoring
        candidate seen anywhere — so the UI can show "we found *something*
        close, here's what".
        """
        from app import _search_spotify_cascade
        mock_search.return_value = [{"name": "irrelevant"}]
        mock_best.side_effect = [
            self._best(0.3, fetched_title="Low score"),
            self._best(0.55, fetched_title="Close but no cigar"),
        ]
        _, rejected = _search_spotify_cascade("A", "T")
        assert rejected is not None
        assert rejected["fetched_title"] == "Close but no cigar"
        assert rejected["match"] == 0.55


class TestMissingCanonicalSections:
    """The post-scrape coverage check: an NMM post should contain all
    7 canonical subgenres. If one is missing after classification, the
    user gets warned.
    """

    def _section(self, name):
        # Minimal stub of SubgenreSection that has the .name attribute
        # _missing_canonical_sections reads. (MagicMock(name=...) sets
        # the mock's repr name, not an attribute, so use a SimpleNamespace.)
        from types import SimpleNamespace
        return SimpleNamespace(name=name)

    def test_no_missing_when_all_buckets_represented(self):
        from app import _missing_canonical_sections
        sections = [
            self._section("General DnB / Mixed"),
            self._section("Dancefloor"),
            self._section("Liquid"),
            self._section("Deep / Tech / Minimal"),
            self._section("Neuro"),
            self._section("Jump Up"),
            self._section("Jungle / Halftime / Experimental"),
        ]
        assert _missing_canonical_sections(sections) == []

    def test_lists_missing_buckets_in_canonical_order(self):
        from app import _missing_canonical_sections
        # Only Liquid and Neuro present.
        sections = [self._section("Liquid"), self._section("Neuro")]
        result = _missing_canonical_sections(sections)
        # Order matches CANONICAL_KEYS (general, dancefloor, ..., jungle_etc)
        # minus the two present ones.
        assert result == ["general", "dancefloor", "deep_tech_min", "jump_up", "jungle_etc"]

    def test_drifted_heading_still_counts_as_present(self):
        """If 'Liquid Vibez' is the heading, the classifier routes it to
        liquid — so the coverage check shouldn't flag liquid as missing.
        """
        from app import _missing_canonical_sections
        sections = [self._section("Liquid Vibez")]
        assert "liquid" not in _missing_canonical_sections(sections)

    def test_unclassifiable_section_doesnt_satisfy_anything(self):
        from app import _missing_canonical_sections, CANONICAL_KEYS
        # Hardcore isn't in any signature; it shouldn't accidentally
        # satisfy any canonical key.
        sections = [self._section("Hardcore")]
        assert _missing_canonical_sections(sections) == list(CANONICAL_KEYS)

class TestAlbumSanityCheck:
    """Step-3 cascade scores against the Beatport track, not the release
    title, so a same-track-different-album hit can sneak through. The
    album_title_match field plus _passes_album_sanity guard against
    auto-applying those.
    """

    def test_passes_when_no_album_score(self):
        from app import _passes_album_sanity
        # Steps 1 & 2 don't emit album_title_match — they shouldn't be
        # gated.
        assert _passes_album_sanity({"match": 0.8}) is True

    def test_passes_when_album_score_high(self):
        from app import _passes_album_sanity
        assert _passes_album_sanity({"match": 0.8, "album_title_match": 0.9}) is True

    def test_fails_when_album_score_low(self):
        from app import _passes_album_sanity
        assert _passes_album_sanity({"match": 0.8, "album_title_match": 0.1}) is False

    def test_verify_release_demotes_low_album_score(self):
        from app import _verify_release
        from models import Release

        release = Release(artists="Various Artists", title="Pioneers Five EP",
                          label="X", links={"Beatport": "https://www.beatport.com/release/x/123"})

        candidate = {
            "match": 0.95, "album_title_match": 0.1, "fetched_title": "Some Unrelated Album",
            "url": "https://spotify/album/xyz", "source": "beatport_track_album_search",
            "service": "Spotify", "artists": "Milzee",
        }
        with patch("app.verify_spotify_link", return_value=None), \
             patch("app.verify_beatport_link", return_value=None), \
             patch("app._search_spotify_cascade", return_value=(candidate, None)):
            _verify_release(release)

        # Not auto-applied
        assert "Spotify" not in release.links
        assert release.spotify_auto is False
        # But surfaced as a rejected candidate
        assert release.spotify_search_rejected == candidate

    def test_verify_release_accepts_high_album_score(self):
        from app import _verify_release
        from models import Release

        release = Release(artists="Various Artists", title="Pioneers Five EP",
                          label="X", links={"Beatport": "https://www.beatport.com/release/x/123"})

        candidate = {
            "match": 0.95, "album_title_match": 0.9, "fetched_title": "Pioneers Five",
            "url": "https://spotify/album/xyz", "source": "beatport_track_album_search",
            "service": "Spotify", "artists": "Milzee",
        }
        with patch("app.verify_spotify_link", return_value=None), \
             patch("app.verify_beatport_link", return_value=None), \
             patch("app._search_spotify_cascade", return_value=(candidate, None)):
            _verify_release(release)

        assert release.links["Spotify"] == "https://spotify/album/xyz"
        assert release.spotify_auto is True
