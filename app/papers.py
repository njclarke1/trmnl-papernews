"""The papers: display names, preferred fallback layouts, and how each is sourced.

URL patterns and feeds are best guesses until the Phase 1 probe (tools/probe_bodies.py) has run on
the NAS; fix them from the probe's saved raw HTML, not from memory.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Paper:
    key: str
    name: str                      # typeset nameplate stand-in (real wordmark replaces it when cached)
    np: str                        # nameplate style class
    short: str                     # "the FT"
    source: str                    # adapter: guardian | web
    fallback_layout: str           # layout when there is no free body: summary | poster | briefing | bulletin
    home: str = ""
    article_re: str = ""
    rss: tuple = ()
    print_front: bool = False      # OCR the printed front for body text (no free web text)
    front_slug: str = ""           # frontpages.com slug
    front_finder: str = "frontpages"   # frontpages | tomorrowspapers
    review_names: tuple = ()       # how PA's "What the papers say" names the paper
    note_web: str = ""
    note_print: str = ""


PAPERS: dict[str, Paper] = {p.key: p for p in [
    Paper("guardian", "The Guardian", "gu", "the Guardian", "guardian", "summary",
          review_names=("The Guardian", "Guardian"),
          note_web="Guardian Content API · editors’ picks", note_print="Print splash · Guardian Content API"),
    Paper("bbc", "BBC NEWS", "bbc", "the BBC", "web", "bulletin",
          home="https://www.bbc.co.uk/news", article_re=r"^/news/(articles/[a-z0-9]{10,}|[a-z0-9_-]+-\d{7,})$",
          rss=("https://feeds.bbci.co.uk/news/rss.xml",),
          note_web="bbc.co.uk/news · homepage order"),
    Paper("ft", "FINANCIAL TIMES", "ft", "the FT", "web", "summary",
          home="https://www.ft.com", article_re=r"^/content/[0-9a-f-]{36}$",
          rss=("https://www.ft.com/rss/home/uk", "https://www.ft.com/rss/home"),
          print_front=True, front_slug="financial-times",
          review_names=("Financial Times", "The FT", "FT"),
          note_web="ft.com · headlines (articles paywalled)", note_print="Printed front page · text by OCR"),
    Paper("times", "THE TIMES", "tm", "the Times", "web", "summary",
          home="https://www.thetimes.com", article_re=r"^/.*/article/[a-z0-9-]+$",
          rss=("https://openrss.org/www.thetimes.com",),
          print_front=True, front_slug="the-times", front_finder="tomorrowspapers",
          review_names=("The Times", "Times"),
          note_web="thetimes.com · headlines (articles paywalled)", note_print="Printed front page · text by OCR"),
    Paper("indy", "THE INDEPENDENT", "in", "the Independent", "web", "poster",
          home="https://www.independent.co.uk",
          article_re=r"^/(news|politics|world|business|climate-change|tech|life-style|voices|sport)/.+-[ab]?\d{6,}\.html$",
          rss=("https://www.independent.co.uk/news/uk/rss", "https://www.independent.co.uk/rss"),
          review_names=("The Independent", "Independent"),
          note_web="independent.co.uk · homepage order"),
    Paper("i", "i", "i", "the i", "web", "briefing",
          home="https://inews.co.uk", article_re=r"^/(news|opinion|money|sport|culture|inews-lifestyle)/.+-\d{6,}/?$",
          rss=("https://inews.co.uk/feed",),
          review_names=("The i", "i paper", "i"),
          note_web="inews.co.uk · homepage order", note_print="Print splash · inews.co.uk"),
    Paper("telegraph", "The Daily Telegraph", "tg", "the Telegraph", "web", "summary",
          home="https://www.telegraph.co.uk", article_re=r"^/[a-z-]+(/[a-z-]+)*/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/?$",
          rss=("https://www.telegraph.co.uk/rss.xml",),
          review_names=("The Daily Telegraph", "The Telegraph", "Telegraph"),
          note_web="telegraph.co.uk · homepage order", note_print="Print splash · telegraph.co.uk"),
    # The New York Times (added 21 Oct 2026): editors' order from the Top Stories API (free key);
    # morning body text by OCR of the NYT's own printed front-page PDF (high resolution).
    Paper("nyt", "The New York Times", "ny", "the NYT", "nyt", "summary",
          print_front=True, front_finder="nytpdf",
          note_web="NYT Top Stories API · editors’ order", note_print="Printed front page · text by OCR"),
    # Weeklies (added 21 Oct 2026): shown with their DAILY web stories, styled as their own paper.
    # Not in PA's daily round-up, so never print_front.
    Paper("newworld", "The New World", "nw", "the New World", "web", "summary",
          home="https://www.thenewworld.co.uk", article_re=r"^/(?:[a-z0-9-]+/)?[a-z0-9]+(?:-[a-z0-9]+){3,}/?$",
          rss=("https://www.thenewworld.co.uk/feed/", "https://www.thenewworld.co.uk/rss"),
          note_web="thenewworld.co.uk · homepage order"),
    Paper("economist", "The Economist", "ec", "the Economist", "web", "summary",
          home="https://www.economist.com", article_re=r"^/[a-z-]+/\d{4}/\d{2}/\d{2}/[a-z0-9-]+$",
          rss=("https://www.economist.com/latest/rss.xml", "https://www.economist.com/the-world-this-week/rss.xml"),
          note_web="economist.com · headlines (articles paywalled)"),
    Paper("spectator", "THE SPECTATOR", "sp", "the Spectator", "web", "summary",
          home="https://www.spectator.co.uk", article_re=r"^/article/[a-z0-9-]+/?$",
          rss=("https://www.spectator.co.uk/feed/",),
          note_web="spectator.co.uk · homepage order"),
]}

# Balance (21 Oct 2026): left of centre — Guardian, New World, NYT; centre — BBC, FT, i, Economist;
# right of centre — Times, Telegraph, Spectator. Interleaved so consecutive papers differ in leaning.
# The Independent stays defined but out of the default rotation.
DEFAULT_ROTATION = "guardian,bbc,times,nyt,ft,telegraph,newworld,economist,spectator,i"
