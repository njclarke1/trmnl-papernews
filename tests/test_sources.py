import json

import pytest

from app.papers import PAPERS
from app.sources import papers_review, printfront
from app.sources.base import Http, ShapeError, SourceUnavailable, best_match, read_article
from app.sources.guardian import GuardianAdapter
from app.sources.web import WebAdapter, homepage_links
from conftest import FakeHttp, Resp

BODY = " ".join(["Ministers have approved the first stage of the scheme after a decade of delay."] * 12)


def article(headline, free=True, body=BODY, section="Politics", ld_type="NewsArticle", gated_part=False):
    ld = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, {
        "@type": [ld_type], "headline": headline, "description": f"Standfirst for {headline}",
        "articleSection": [section], "author": [{"@type": "Person", "name": "A Reporter"}],
        "isAccessibleForFree": "true" if free else "false", "articleBody": body,
        "image": [{"@type": "ImageObject", "url": "https://img.example/x.jpg"}],
        **({"hasPart": {"@type": "WebPageElement", "isAccessibleForFree": "False", "cssSelector": ".paywall"}} if gated_part else {})}]}
    paras = "".join(f"<p>{s}.</p>" for s in body.split(". ") if s)
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body><article><h1>{headline}</h1>{paras}</article></body></html>'


def bbc_routes(n_links=6, lead_free=True):
    links = "".join(f'<a href="/news/articles/c{i:011d}">Story {i}</a>' for i in range(n_links))
    routes = {"https://feeds.bbci.co.uk": "<rss><channel>" + "".join(
        f"<item><title>RSS {i}</title><link>https://www.bbc.co.uk/news/articles/c{i:011d}</link><description>Sum {i}</description></item>"
        for i in range(8)) + "</channel></rss>",
        "https://www.bbc.co.uk/news/articles/c00000000000": article("France imposes curfews after riots", free=lead_free),
        "https://www.bbc.co.uk/news/articles/": article("Another story"),
        "https://www.bbc.co.uk/news": f"<html><body><a href='/sport'>x</a>{links}</body></html>"}
    return routes


# ---------------------------------------------------------------- web adapter
def test_homepage_links_by_pattern_in_page_order():
    html = '<a href="/news/articles/cabcdefghij1">A</a><a href="/sport/x">no</a><a href="https://www.bbc.co.uk/news/articles/cabcdefghij1">dup</a><a href="/news/uk-12345678">B</a>'
    got = homepage_links(html, "https://www.bbc.co.uk/news", PAPERS["bbc"].article_re)
    assert [t for _, t in got] == ["A", "B"]


def test_web_front_with_body():
    ed = WebAdapter(PAPERS["bbc"], FakeHttp(bbc_routes())).build("pm", "2026-10-21")
    assert ed.kind == "web_front" and ed.lead.headline == "France imposes curfews after riots"
    assert ed.lead.body_chars > 900 and ed.lead.byline == "A Reporter" and ed.lead.section == "Politics"
    assert len(ed.rail) >= 3 and all(not s.paragraphs for s in ed.rail)


def test_paid_article_never_keeps_body():
    ed = WebAdapter(PAPERS["bbc"], FakeHttp(bbc_routes(lead_free=False))).build("pm", "2026-10-21")
    assert ed.lead.is_free is False and ed.lead.paragraphs == []


def test_gated_haspart_counts_as_paid(fake_http):
    st = read_article(FakeHttp({"https://x/": article("Gated", gated_part=True)}), "https://x/a")
    assert st.is_free is False and st.paragraphs == []


def test_too_few_homepage_links_falls_back_to_rss_latest():
    ed = WebAdapter(PAPERS["bbc"], FakeHttp(bbc_routes(n_links=2))).build("pm", "2026-10-21")
    assert ed.kind == "latest"


def test_homepage_blocked_and_no_rss_is_unavailable():
    routes = {"https://www.bbc.co.uk/news": SourceUnavailable("403")}
    with pytest.raises(SourceUnavailable):
        WebAdapter(PAPERS["bbc"], FakeHttp(routes)).build("pm", "2026-10-21")


def test_review_promotes_print_splash():
    review = papers_review.ReviewEntry("bbc", "The BBC leads on story number four's big development", "")
    routes = bbc_routes()
    routes = {"https://www.bbc.co.uk/news/articles/c00000000004": article("Story four big development"), **routes}
    http = FakeHttp(routes)
    adapter = WebAdapter(PAPERS["bbc"], http)
    kind, cands = adapter.candidates()
    cands[4].headline = "Story four big development"
    adapter.candidates = lambda: (kind, cands)
    ed = adapter.build("am", "2026-10-21", review)
    assert ed.kind == "print_front" and ed.lead.headline == "Story four big development"


def test_challenge_page_detected(monkeypatch):
    h = Http()

    class S:
        headers = {}

        def get(self, url, **kw):
            return Resp(url, "<html><title>Just a moment...</title><div id='cf-chl'></div></html>")
    h.s = S()
    with pytest.raises(SourceUnavailable):
        h.get("https://www.telegraph.co.uk/")


def test_best_match_threshold():
    from app.model import Story
    stories = [Story("Chancellor rules out capital gains tax rise"), Story("Burnham orders Thames Water rethink")]
    assert best_match("The Guardian leads on Burnham ordering a rethink over Thames Water", stories) == 1
    assert best_match("Pride of Britain winners celebrated", stories) is None


# ---------------------------------------------------------------- guardian
def g_item(i, typ="article", tone="tone/news", body=True):
    return {"id": f"x/{i}", "type": typ, "sectionName": "Politics", "webTitle": f"Pick {i}", "webUrl": f"https://www.theguardian.com/x/{i}",
            "fields": {"headline": f"Pick {i} headline", "standfirst": f"<p>Standfirst {i}</p>", "byline": "Political staff",
                       "body": "<p>" + "</p><p>".join(BODY.split(". ")) + "</p>" if body else ""},
            "tags": [{"id": tone}],
            "elements": [{"type": "image", "relation": "main", "assets": [
                {"file": f"https://i.guim.co.uk/{i}/500.jpg", "typeData": {"width": 500, "caption": "Cap", "credit": "Credit"}},
                {"file": f"https://i.guim.co.uk/{i}/1000.jpg", "typeData": {"width": 1000, "caption": "Cap", "credit": "Credit"}}]}]}


def test_guardian_picks_skip_comment(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "GUARDIAN_API_KEY", "k")
    picks = [g_item(0, tone="tone/comment")] + [g_item(i) for i in range(1, 7)]
    http = FakeHttp({"https://content.guardianapis.com/uk": Resp("u", json_data={"response": {"editorsPicks": picks}})})
    ed = GuardianAdapter(http).build("pm", "2026-10-21")
    assert ed.lead.headline == "Pick 1 headline" and ed.kind == "web_front"
    assert ed.lead.image_url.endswith("1000.jpg") and ed.lead.credit == "Credit"
    assert len(ed.lead.paragraphs) > 3 and ed.lead.standfirst == "Standfirst 1"


def test_guardian_missing_key_is_unavailable(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "GUARDIAN_API_KEY", "")
    with pytest.raises(SourceUnavailable):
        GuardianAdapter(FakeHttp({})).build("pm", "2026-10-21")


def test_guardian_shape_error_on_too_few_picks(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "GUARDIAN_API_KEY", "k")
    http = FakeHttp({"https://content.guardianapis.com/uk": Resp("u", json_data={"response": {"editorsPicks": [g_item(1)]}})})
    with pytest.raises(ShapeError):
        GuardianAdapter(http).build("pm", "2026-10-21")


# ---------------------------------------------------------------- PA papers review
REVIEW = """<article>
<p>The Guardian leads on Andy Burnham ordering a "radical rethink of options to fix Thames Water".</p>
<p>The Financial Times reports challenges facing Macron deepen after street clashes reignite a French debt rout.</p>
<p>The Times says there will be "no increase to capital gains tax in the Budget" and curfews in riot-torn French cities.</p>
<p>The Daily Telegraph says Labour has cut £2bn from the Army front line.</p>
<p>The i reports the two-child cap on childcare benefits will be lifted by April.</p>
<p>The Independent leads with a curfew imposed to stop France riots as unrest grips Europe.</p>
</article>"""


def test_review_parser_names_and_quotes():
    r = papers_review.parse(REVIEW)
    assert set(r) >= {"guardian", "ft", "times", "telegraph", "i", "indy"}
    assert r["guardian"].quoted == "radical rethink of options to fix Thames Water"
    assert "Macron" in r["ft"].summary and "Financial" not in r["times"].summary
    assert "childcare" in r["i"].summary


# ---------------------------------------------------------------- print fronts
def test_printfront_rejects_wrong_date(monkeypatch):
    from datetime import date
    http = FakeHttp({"https://www.frontpages.com/financial-times":
                     '<meta property="og:image" content="https://www.frontpages.com/g/2026/10/20/financial-times-1.webp.jpg">'})
    assert printfront.find_image(http, PAPERS["ft"], date(2026, 10, 21)) is None


def test_printfront_small_image_cached_as_failure(monkeypatch):
    import io
    from datetime import date
    from PIL import Image
    buf = io.BytesIO(); Image.new("L", (600, 900), 255).save(buf, "JPEG")
    http = FakeHttp({"https://www.frontpages.com/financial-times":
                     '<meta property="og:image" content="https://www.frontpages.com/g/2026/10/21/ft.jpg">',
                     "https://www.frontpages.com/g/": Resp("x", content=buf.getvalue())})
    assert printfront.splash(http, PAPERS["ft"], date(2026, 10, 21)) is None
    n = len(http.calls)
    assert printfront.splash(http, PAPERS["ft"], date(2026, 10, 21)) is None
    assert len(http.calls) == n                 # cached: not fetched or OCR'd again
