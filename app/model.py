"""Source-neutral model. Adapters produce these; nothing source-specific leaks past them."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields


@dataclass
class Story:
    headline: str
    standfirst: str = ""
    paragraphs: list[str] = field(default_factory=list)
    byline: str = ""
    section: str = ""
    url: str = ""
    image_url: str = ""
    caption: str = ""
    credit: str = ""
    is_free: bool | None = None          # publisher's own flag; False ⇒ never show body text
    live: bool = False

    @property
    def body_chars(self) -> int:
        return sum(len(p) for p in self.paragraphs)


@dataclass
class Edition:
    paper: str
    kind: str                            # print_front | web_front | latest
    lead: Story
    rail: list[Story] = field(default_factory=list)
    continued_page: str | int | None = None   # printed "Continued on page N" ("4", "A8"); int in old files
    note: str = ""                       # attribution line for the footer
    built_at: str = ""                   # ISO time the edition was built
    stale: bool = False                  # carried over from an earlier build (CACHED)
    data_check: bool = False             # a shape check failed (DATA CHECK)
    layout: str = ""                     # chosen at build time, locked with the edition
    image: str = ""                      # processed photo URL for that layout (served from the volume)
    rail_front: int = -1                 # how many rail stories are from the printed front (-1: all)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Edition":
        """Tolerant of fields added or removed between versions (stored editions outlive deploys)."""
        names = {f.name for f in fields(Edition)}
        d = {k: v for k, v in d.items() if k in names}
        d["lead"] = story_from(d["lead"])
        d["rail"] = [story_from(s) for s in d.get("rail", [])]
        return Edition(**d)


def story_from(d: dict) -> Story:
    names = {f.name for f in fields(Story)}
    return Story(**{k: v for k, v in d.items() if k in names})
