"""Persistent + yearly playlist mappings, keyed by a canonical subgenre
key (e.g. "liquid", "neuro") rather than by the exact Reddit section
heading. Inbound section names are routed through `classify_section`,
which scores them against a small signature table.

This is more robust than exact-name matching because the Reddit author
can drift the wording ("Liquid Vibez", "Deep & Minimal Tech",
"Neurofunk Special") without breaking the persistent-playlist write.

Each NMM run writes the section's new tracks into the persistent
playlist (replacing its previous contents after saving them to a dated
backup playlist) and appends them to the yearly playlist. The yearly
playlists are refreshed manually once a year — the app never resets
them.

When a section can't be classified, the caller falls back to creating
a standalone one-off playlist and flags it in the UI.

Beatport will get the same treatment in a follow-up; the structure
here is service-namespaced so adding BEATPORT_PERSISTENT_PLAYLISTS /
BEATPORT_YEARLY_PLAYLISTS later is a drop-in.
"""
from __future__ import annotations

import re


# Canonical keys for the 7 subgenre buckets the persistent playlists
# live in. These are the only keys SPOTIFY_PERSISTENT_PLAYLISTS and
# SPOTIFY_YEARLY_PLAYLISTS use.
CANONICAL_KEYS = (
    "general",
    "dancefloor",
    "liquid",
    "deep_tech_min",
    "neuro",
    "jump_up",
    "jungle_etc",
)


# Human-readable label per canonical key — used when surfacing
# "expected section X was missing" to the UI. Keep these aligned with
# the headings the Reddit author conventionally uses, since that's
# what the user will be scanning for in the post.
CANONICAL_DISPLAY_NAMES: dict[str, str] = {
    "general":       "General DnB / Mixed",
    "dancefloor":    "Dancefloor",
    "liquid":        "Liquid",
    "deep_tech_min": "Deep / Tech / Minimal",
    "neuro":         "Neuro",
    "jump_up":       "Jump Up",
    "jungle_etc":    "Jungle / Halftime / Experimental",
}


# Token signatures for the classifier. An input section name is
# tokenised (lowercase, non-alphanumeric stripped) and compared against
# each signature. For every signature token we check whether any input
# token *starts with* it — this makes "neurofunk" match `neuro`,
# "jungles" match `jungle`, etc.
#
# Best-match rule: primary key is number of signature tokens matched;
# tiebreak is precision (matches / signature size). If the top two
# bucket scores tie on both, we return None (genuinely ambiguous) and
# the section falls through to the unmapped path.
#
# Notes on individual signatures:
#   - "general" deliberately omits "dnb" (every section is dnb).
#   - "jump_up": just "jump" — "up" alone matches far too many things.
#   - "neuro" prefix-matches "neurofunk", "neurobass", etc.
SECTION_SIGNATURES: dict[str, set[str]] = {
    "general":       {"general", "mixed"},
    "dancefloor":    {"dancefloor"},
    "liquid":        {"liquid"},
    "deep_tech_min": {"deep", "tech", "minimal"},
    "neuro":         {"neuro"},
    "jump_up":       {"jump"},
    "jungle_etc":    {"jungle", "halftime", "experimental"},
}


# Persistent playlists, keyed by canonical key.
SPOTIFY_PERSISTENT_PLAYLISTS: dict[str, str] = {
    "general":       "https://open.spotify.com/playlist/7FZVGL79VzXN2AuAJnmLgb",
    "dancefloor":    "https://open.spotify.com/playlist/6a0386TH7lWRrr7r1PkXqK",
    "liquid":        "https://open.spotify.com/playlist/7KDeMsdoG5XxZkLl7y8XYU",
    "deep_tech_min": "https://open.spotify.com/playlist/5Ol6R25Eg6yS9UDAwxcnmd",
    "neuro":         "https://open.spotify.com/playlist/4Qwxw3reKaYYZRWangAGA0",
    "jump_up":       "https://open.spotify.com/playlist/7B3ZINJbXgnEeEah8pK7la",
    "jungle_etc":    "https://open.spotify.com/playlist/3xRC7V0Qh87QxA3UpZqdXS",
}

# Yearly playlists, same keys.
SPOTIFY_YEARLY_PLAYLISTS: dict[str, str] = {
    "general":       "https://open.spotify.com/playlist/2oDD431zPdRxZtzIawQWZN",
    "dancefloor":    "https://open.spotify.com/playlist/10PTZH39n1O8IB2333O68P",
    "liquid":        "https://open.spotify.com/playlist/72CiRJ6ZOMrMPV0GZJq2yo",
    "deep_tech_min": "https://open.spotify.com/playlist/3en39eU2pdxnztvjOPKRFl",
    "neuro":         "https://open.spotify.com/playlist/44kCJmIcjzLht6yuCxo9Pt",
    "jump_up":       "https://open.spotify.com/playlist/53uzqhEx7oE3Kapop52NIX",
    "jungle_etc":    "https://open.spotify.com/playlist/6z7CB4ZySkUqf6CaRr2ndf",
}


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenise(name: str) -> list[str]:
    return _TOKEN_RE.findall((name or "").lower())


def _score(tokens: list[str], signature: set[str]) -> tuple[int, float]:
    """(match_count, precision). A signature token matches if any input
    token *starts with* it — so 'neuro' matches 'neurofunk' and
    'jungle' matches 'jungles'."""
    matches = sum(1 for s in signature if any(t.startswith(s) for t in tokens))
    if matches == 0:
        return (0, 0.0)
    return (matches, matches / len(signature))


def classify_section(name: str) -> str | None:
    """Map a raw Reddit section heading to one of CANONICAL_KEYS.

    Returns None when nothing matches, or when the top two buckets are
    indistinguishable on both match count and precision (genuinely
    ambiguous — let the caller surface the warning).
    """
    tokens = _tokenise(name)
    if not tokens:
        return None

    scored: list[tuple[tuple[int, float], str]] = []
    for key, sig in SECTION_SIGNATURES.items():
        score = _score(tokens, sig)
        if score[0] > 0:
            scored.append((score, key))

    if not scored:
        return None

    scored.sort(reverse=True)
    top_score, top_key = scored[0]
    if len(scored) > 1 and scored[1][0] == top_score:
        # Two buckets tied on both count and precision — genuinely
        # ambiguous. Refuse to pick rather than coin-flip.
        return None
    return top_key


def lookup_spotify_playlists(section_name: str) -> dict[str, str] | None:
    """Classify a Reddit section name and return {persistent, yearly}
    URLs for the matching canonical bucket, or None if unclassifiable.

    `yearly` may be None if a bucket has a persistent but no yearly
    playlist configured (defensive — current config has both for all
    buckets).
    """
    key = classify_section(section_name)
    if not key:
        return None
    persistent = SPOTIFY_PERSISTENT_PLAYLISTS.get(key)
    if not persistent:
        return None
    return {
        "persistent": persistent,
        "yearly": SPOTIFY_YEARLY_PLAYLISTS.get(key),
        "canonical_key": key,
    }
