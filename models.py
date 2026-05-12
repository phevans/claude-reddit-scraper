from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Release:
    artists: str
    title: str
    label: str
    links: dict[str, str] = field(default_factory=dict)
    spotify_match: float | None = None
    spotify_title: str | None = None
    beatport_match: float | None = None
    beatport_title: str | None = None
    beatport_release_id: int | None = None
    beatport_track_ids: list[int] = field(default_factory=list)
    spotify_auto: bool = False
    spotify_search_rejected: dict | None = None


@dataclass
class SubgenreSection:
    name: str
    releases: list[Release] = field(default_factory=list)
