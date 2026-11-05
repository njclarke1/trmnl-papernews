"""Google News RSS search, scoped to one site: headlines for papers whose own site refuses us (the Telegraph).

Google's public RSS output: each item is "Headline - Publisher" with a news.google.com link, a date and
the publisher as <source>. No summaries or photos, so it fills a headline list, never a body. Order is
Google's ranking for the last day, not the paper's own running order: it is labelled as such.
"""
from __future__ import annotations

import urllib.parse as up

from bs4 import BeautifulSoup

from .. import text
from ..model import Story
from .base import Http, SourceUnavailable

URL = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"


def items(xml: str, publisher: str) -> list[Story]:
    """Headlines from `publisher` (matched against <source>), with Google's " - Publisher" suffix removed."""
    out, seen = [], set()
    for it in BeautifulSoup(xml, "xml").find_all("item"):
        src = it.find("source")
        name = text.clean(src.text) if src is not None else ""
        if src is not None and publisher.lower() not in (src.get("url", "") + name).lower():
            continue                                    # site: also matches syndicated copies elsewhere
        head = text.clean(it.title.text if it.title else "")
        if name and head.endswith(f" - {name}"):
            head = head[: -len(name) - 3].strip()
        if head and head.lower() not in seen:
            seen.add(head.lower())
            out.append(Story(headline=head, url=text.clean(it.link.text if it.link else ""), is_free=False))
    return out


def headlines(http: Http, queries: tuple[str, ...], publisher: str) -> list[Story]:
    """The first query that gives at least 4 headlines (news-only before whole-site)."""
    for q in queries:
        try:
            got = items(http.feed(URL.format(q=up.quote_plus(q))), publisher)
        except SourceUnavailable:
            continue
        if len(got) >= 4:
            return got
    return []
