"""BBC News's nightly newspaper review ("Newspaper headlines: …"): a second, independent source for
each paper's printed splash alongside PA's round-up (app/sources/papers_review.py).

The index URL and article format are unverified until the probe runs. Discovery is deliberately
narrow: a link whose text starts like the BBC's review titles, then a date check on the article's own
JSON-LD (published the evening before, or that morning). Anything else ⇒ {} (PA alone is used).
"""
from __future__ import annotations

import re
import urllib.parse as up
from datetime import date, timedelta

from bs4 import BeautifulSoup

from .base import Http, SourceUnavailable, jsonld_nodes
from .papers_review import ReviewEntry, parse

INDEXES = ("https://www.bbc.co.uk/news/blogs/the_papers", "https://www.bbc.co.uk/news/uk")
TITLE = re.compile(r"^\s*(newspaper headlines|the papers)\b", re.I)


def _published(html: str) -> date | None:
    for n in jsonld_nodes(html):
        v = n.get("datePublished") or ""
        if re.match(r"\d{4}-\d{2}-\d{2}", v):
            return date.fromisoformat(v[:10])
    return None


def fetch(http: Http, day_iso: str) -> dict[str, ReviewEntry]:
    day = date.fromisoformat(day_iso)
    for index in INDEXES:
        try:
            soup = BeautifulSoup(http.get(index).text, "lxml")
        except SourceUnavailable:
            continue
        links = [up.urljoin(index, a["href"]) for a in soup.find_all("a", href=True)
                 if TITLE.search(a.get_text(" ")) and "/news/" in a["href"]]
        for url in list(dict.fromkeys(links))[:3]:
            html = http.get(url).text
            when = _published(html)
            if when is not None and day - timedelta(days=1) <= when <= day:
                return parse(html)
    return {}
