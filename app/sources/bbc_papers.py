"""BBC News's nightly newspaper review: a second, independent source for each paper's printed
splash alongside PA's round-up (app/sources/papers_review.py).

Index: the "The Papers" topic page (24 Oct probe: /news/blogs/the_papers redirects to
/news/topics/cpml2v678pxt). Review titles quote two splashes ("'Meghan warned over father' and
'Don't risk Truss-style budget'"), so links are not matched by title: the newest /news/articles/
links are tried in order and the first published the evening before `day` (18:00 to 10:00 London)
that names at least three papers is used. The article's own format is still unverified (probe v2).
"""
from __future__ import annotations

import urllib.parse as up
from datetime import date, datetime, time, timedelta

from bs4 import BeautifulSoup

from .. import clock
from .base import Http, SourceUnavailable, jsonld_nodes
from .papers_review import ReviewEntry, parse

INDEXES = ("https://www.bbc.co.uk/news/topics/cpml2v678pxt", "https://www.bbc.co.uk/news/blogs/the_papers")
TRY = 4


def published(html: str) -> datetime | None:
    for n in jsonld_nodes(html):
        v = n.get("datePublished") or ""
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(clock.LONDON)
        except ValueError:
            continue
    return None


def for_day(when: datetime | None, day: date) -> bool:
    if when is None:
        return False
    start = datetime.combine(day - timedelta(days=1), time(18, 0), tzinfo=clock.LONDON)
    end = datetime.combine(day, time(10, 0), tzinfo=clock.LONDON)
    return start <= when <= end


def fetch(http: Http, day_iso: str) -> dict[str, ReviewEntry]:
    day = date.fromisoformat(day_iso)
    for index in INDEXES:
        try:
            r = http.get(index)
        except SourceUnavailable:
            continue
        base = getattr(r, "url", "") or index
        links = [up.urljoin(base if base.startswith("http") else index, a["href"])
                 for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True) if "/news/articles/" in a["href"]]
        for url in list(dict.fromkeys(links))[:TRY]:
            html = http.get(url).text
            if not for_day(published(html), day):
                continue
            got = parse(html)
            if len(got) >= 3:
                return got
        if links:
            return {}
    return {}
