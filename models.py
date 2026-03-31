from dataclasses import dataclass, field


@dataclass
class Release:
    artists: str
    title: str
    label: str
    links: dict[str, str] = field(default_factory=dict)
    spotify_match: float | None = None


@dataclass
class SubgenreSection:
    name: str
    releases: list[Release] = field(default_factory=list)
