"""Phase 1 probe: can each paper supply Guardian-level content (ranked lead + body + image)?

Run on the NAS (residential IP; the dev sandbox cannot reach these sites):
  docker run --rm -e DEBIAN_FRONTEND=noninteractive -v ~/fp-probe:/w -w /w python:3.12-slim sh -c \
    "apt-get -qq update && apt-get -qq install -y tesseract-ocr >/dev/null && \
     pip -q install requests beautifulsoup4 lxml trafilatura pillow pytesseract pypdfium2 && python probe_bodies.py"
  (front-page images only: append `fronts`; NYT API + PDF: `nyt`; print metadata + BBC review: `print`;
   Times/Telegraph public XML + honest user agent: `alt`;
   pass -e GUARDIAN_API_KEY=... -e NYT_API_KEY=... to docker run)

Writes ./report.md, ./results.json and ./raw/*.gz (raw responses become test stubs).
Reads only what each site serves an ordinary visitor. No paywall bypassing.
"""
import gzip, json, re, sys, time, pathlib, urllib.parse as up
import requests
from bs4 import BeautifulSoup

OUT = pathlib.Path("."); RAW = OUT / "raw"; RAW.mkdir(exist_ok=True)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
S = requests.Session(); S.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})

PAPERS = {
  "guardian":  dict(home="https://www.theguardian.com/uk",
                    rss=["https://www.theguardian.com/uk/rss"],
                    art=r"^/[a-z-]+(/[a-z-]+)?/\d{4}/[a-z]{3}/\d{2}/[a-z0-9-]+$"),
  "bbc":       dict(home="https://www.bbc.co.uk/news",
                    rss=["https://feeds.bbci.co.uk/news/rss.xml"],
                    art=r"^/news/(articles/[a-z0-9]{10,}|[a-z0-9_-]+-\d{7,})$"),
  "indy":      dict(home="https://www.independent.co.uk",
                    rss=["https://www.independent.co.uk/news/uk/rss", "https://www.independent.co.uk/rss"],
                    art=r"^/(news|politics|world|business|climate-change|tech|life-style|voices|sport)/.+-[ab]?\d{6,}\.html$"),
  "i":         dict(home="https://inews.co.uk",
                    rss=["https://inews.co.uk/feed", "https://inews.co.uk/news/feed"],
                    art=r"^/(news|opinion|money|sport|culture|inews-lifestyle)/.+-\d{6,}/?$"),
  "telegraph": dict(home="https://www.telegraph.co.uk",
                    rss=["https://www.telegraph.co.uk/rss.xml", "https://www.telegraph.co.uk/news/rss.xml"],
                    art=r"^/[a-z-]+(/[a-z-]+)*/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/?$"),
  "times":     dict(home="https://www.thetimes.com",                  # no own RSS since 2023; Open RSS is third-party
                    rss=["https://openrss.org/www.thetimes.com", "https://openrss.org/www.thetimes.com/uk"],
                    art=r"^/.*/article/[a-z0-9-]+$"),
  "newworld":  dict(home="https://www.thenewworld.co.uk",
                    rss=["https://www.thenewworld.co.uk/feed/", "https://www.thenewworld.co.uk/rss"],
                    art=r"^/(?:[a-z0-9-]+/)?[a-z0-9]+(?:-[a-z0-9]+){3,}/?$"),
  "economist": dict(home="https://www.economist.com",
                    rss=["https://www.economist.com/latest/rss.xml", "https://www.economist.com/the-world-this-week/rss.xml"],
                    art=r"^/[a-z-]+/\d{4}/\d{2}/\d{2}/[a-z0-9-]+$"),
  "spectator": dict(home="https://spectator.com",
                    rss=["https://spectator.com/feed/", "https://www.spectator.co.uk/feed/"],
                    art=r"^/article/[a-z0-9-]+/?$"),
  "ft":        dict(home="https://www.ft.com",
                    rss=["https://www.ft.com/rss/home/uk", "https://www.ft.com/rss/home", "https://www.ft.com/?format=rss"],
                    art=r"^/content/[0-9a-f-]{36}$"),
}
PAPERS_REVIEW = "https://www.expressandstar.com/uk-news/"   # PA's nightly "What the papers say" (print splashes)

def get(url, tag):
    try:
        r = S.get(url, timeout=20, allow_redirects=True)
        (RAW / f"{tag}.gz").write_bytes(gzip.compress(r.content))
        return r.status_code, r.text, r.url
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", url

def jsonld_article(html):
    """First NewsArticle-like object from JSON-LD (handles lists and @graph)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop(0)
            if not isinstance(o, dict):
                continue
            stack.extend(o.get("@graph", []) if isinstance(o.get("@graph"), list) else [])
            t = o.get("@type"); t = t if isinstance(t, list) else [t]
            if any(x in ("NewsArticle", "Article", "ReportageNewsArticle", "AnalysisNewsArticle", "LiveBlogPosting") for x in t):
                return o
    return {}

def flag_free(o):
    v = o.get("isAccessibleForFree")
    if isinstance(v, str): v = v.strip().lower() not in ("false", "0", "no")
    part = o.get("hasPart"); part = part if isinstance(part, list) else [part] if part else []
    gated = any(isinstance(p, dict) and str(p.get("isAccessibleForFree")).lower() == "false" for p in part)
    return v, gated

def image_url(o):
    im = o.get("image")
    if isinstance(im, list): im = im[0] if im else None
    if isinstance(im, dict): return im.get("url"), im.get("width")
    return (im, None) if isinstance(im, str) else (None, None)

def probe_article(key, url, n):
    code, html, final = get(url, f"{key}-article-{n}")
    row = {"url": url, "status": code, "final_url": final}
    if code != 200:
        return row
    o = jsonld_article(html)
    free, gated_part = flag_free(o)
    body_ld = o.get("articleBody") or ""
    try:
        import trafilatura
        body_tf = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    except Exception as e:
        body_tf = ""; row["trafilatura_error"] = str(e)
    img, img_w = image_url(o)
    soup = BeautifulSoup(html, "lxml")
    og_img = (soup.find("meta", property="og:image") or {}).get("content")
    section = o.get("articleSection"); section = section[0] if isinstance(section, list) and section else section
    author = o.get("author"); author = author[0] if isinstance(author, list) and author else author
    row.update(
        type=o.get("@type"), headline=o.get("headline"), description=(o.get("description") or "")[:200],
        section=section, author=author.get("name") if isinstance(author, dict) else author,
        isAccessibleForFree=free, gated_part=gated_part,
        body_jsonld_chars=len(body_ld), body_trafilatura_chars=len(body_tf),
        body_sample=(body_ld or body_tf)[:300],
        image=img or og_img, image_width=img_w,
        paywall_markers=[m for m in ("paywall", "subscribe to read", "subscriber-only", "premium", "barrier", "piano")
                         if m in html.lower()][:6],
    )
    return row

def homepage_links(key, cfg):
    code, html, final = get(cfg["home"], f"{key}-home")
    if code != 200:
        return code, []
    base = up.urlparse(final); pat = re.compile(cfg["art"]); seen, links = set(), []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        u = up.urlparse(up.urljoin(final, a["href"]))
        if u.netloc.split(":")[0].removeprefix("www.") != base.netloc.removeprefix("www."):
            continue
        path = u.path.rstrip("/") if key != "i" else u.path
        if pat.match(path) and path not in seen:
            seen.add(path); links.append({"url": f"{u.scheme}://{u.netloc}{u.path}", "text": a.get_text(" ", strip=True)[:120]})
    return code, links

def rss_items(key, urls):
    for n, url in enumerate(urls):
        code, text, _ = get(url, f"{key}-rss-{n}")
        if code == 200 and "<item" in text:
            soup = BeautifulSoup(text, "xml")
            items = [{"title": (i.title.text if i.title else ""), "link": (i.link.text if i.link else ""),
                      "has_body": bool(i.find("content:encoded") or i.find("encoded")),
                      "has_image": bool(i.find("media:thumbnail") or i.find("media:content") or i.find("enclosure"))}
                     for i in soup.find_all("item")[:10]]
            return url, code, items
    return urls[0], code, []

def robots_sitemaps(key, home):
    u = up.urlparse(home); code, text, _ = get(f"{u.scheme}://{u.netloc}/robots.txt", f"{key}-robots")
    return [l.split(":", 1)[1].strip() for l in (text or "").splitlines() if l.lower().startswith("sitemap:")] if code == 200 else []

results = {}
args = sys.argv[1:]
only = [a for a in args if a in PAPERS] or ([] if args else list(PAPERS))
for key in only:
    cfg = PAPERS[key]; print(f"== {key}", flush=True)
    r = {}
    r["home_status"], links = homepage_links(key, cfg); r["home_links"] = links[:12]
    r["rss_url"], r["rss_status"], r["rss_items"] = rss_items(key, cfg["rss"])
    r["sitemaps"] = [s for s in robots_sitemaps(key, cfg["home"]) if "news" in s.lower()][:5]
    targets = [l["url"] for l in links[:3]] or [i["link"] for i in r["rss_items"][:3]]
    r["articles"] = []
    for n, u in enumerate(targets):
        r["articles"].append(probe_article(key, u, n)); time.sleep(2)      # polite pacing
    results[key] = r; time.sleep(2)

if not sys.argv[1:]:
    code, html, final = get(PAPERS_REVIEW, "papers-review-index")
    hits = sorted({up.urljoin(final, a["href"]) for a in BeautifulSoup(html or "", "lxml").find_all("a", href=True)
                   if "what-the-papers-say" in a["href"]}) if code == 200 else []
    results["_papers_review"] = {"index_status": code, "links": hits[:5]}
    if hits:
        c2, h2, _ = get(hits[0], "papers-review-latest")
        results["_papers_review"]["latest_status"] = c2
        results["_papers_review"]["latest_text"] = BeautifulSoup(h2 or "", "lxml").get_text(" ", strip=True)[:3000]

# ---- published front-page images: resolution + OCR legibility ----
# frontpages.com carries only the FT among our UK papers (24 Oct probe), filed under its upload time
# (the evening before). tomorrowspapers.co.uk carries the rest, but its pages don't name the paper:
# each image's masthead band is OCR'd to identify it, as the app does.
FRONTS = {"ft": "financial-times"}
def front_image(key, slug):
    code, html, final = get(f"https://www.frontpages.com/{slug}", f"{key}-front-page")
    row = {"page_status": code}
    if code != 200:
        return row
    og = BeautifulSoup(html, "lxml").find("meta", property="og:image")
    row["image_url"] = og.get("content") if og else None
    if row["image_url"] and "/g/" in row["image_url"]:
        row.update({k: v for k, v in _ocr_url(key, row["image_url"]).items() if k != "image_url"})
    return row

MASTHEADS = [("ft", r"financial\s*times|\bft\.com\b"),
             ("times", r"(?<!sunday )(?<!york )(?<!financial )\bthe\s*times\b(?! of)|thetimes\.(?:com|co\.uk)"),
             ("telegraph", r"(?<!sunday )\btelegraph\b|telegraph\.co\.uk"),
             ("guardian", r"\bthe\s*guardian\b|theguardian\.com"), ("i", r"inews\.co\.uk")]
def tp_scan():
    """Every tomorrowspapers.co.uk edition for today and tomorrow: image size, masthead OCR, identity."""
    import datetime as dt
    code, html, final = get("https://www.tomorrowspapers.co.uk/", "tp-index")
    if code != 200:
        return {"index_status": code}
    days = {str(dt.date.today()), str(dt.date.today() + dt.timedelta(days=1))}
    entries = [up.urljoin(final, a["href"]) for a in BeautifulSoup(html, "lxml").find_all("a", href=True)
               if re.search(r"/(\d{4}-\d{2}-\d{2})-[0-9a-f]{6}/?$", a["href"]) and
               re.search(r"/(\d{4}-\d{2}-\d{2})-", a["href"]).group(1) in days]
    rows, saved = [], set()
    for n, u in enumerate(list(dict.fromkeys(entries))[:40]):
        c, h, _ = get(u, f"tp-{n}")
        if c != 200:
            rows.append({"entry": u, "status": c}); continue
        imgs = [i.get("src") for i in BeautifulSoup(h, "lxml").find_all("img") if "/wp-content/uploads/20" in (i.get("src") or "")]
        imgs = [i for i in imgs if "Logo" not in i]
        row = {"entry": u, "image": imgs[0] if imgs else None}
        if imgs:
            try:
                r = S.get(imgs[0], timeout=30)
                from PIL import Image; import io, pytesseract
                im = Image.open(io.BytesIO(r.content)).convert("L"); row["size"] = im.size
                band = im.crop((0, 0, im.width, int(im.height * 0.16)))
                band = band.resize((band.width * 2, band.height * 2), Image.LANCZOS) if band.width < 2000 else band
                txt = re.sub(r"\s+", " ", pytesseract.image_to_string(band, config="--psm 3")).lower()
                row["masthead_text"] = txt[:160]
                key = None if re.search(r"\bsunday\b|\bobserver\b", txt) else \
                    next((k for k, pat in MASTHEADS if re.search(pat, txt)), None)
                row["identified"] = key
                if key and key not in saved:
                    saved.add(key); (RAW / f"{key}-tp-front.jpg").write_bytes(r.content)
                    if key in ("times", "telegraph"):
                        row["ocr"] = {k: v for k, v in _ocr_url(f"{key}-tp", imgs[0]).items() if k != "image_url"}
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
        rows.append(row); time.sleep(1)
    return {"index_status": code, "entries": len(entries), "identified": sorted(saved), "rows": rows}

def _ocr_url(key, url):
    row = {"image_url": url}
    try:
        r = S.get(url, timeout=30); (RAW / f"{key}-front.jpg").write_bytes(r.content)
        from PIL import Image; import io, pytesseract
        im = Image.open(io.BytesIO(r.content)).convert("L"); row["size"] = im.size
        big = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        d = pytesseract.image_to_data(big, config="--psm 3", output_type=pytesseract.Output.DICT)
        words = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
        good = [w for w, c in words if c >= 80]
        row.update(ocr_words=len(words), ocr_words_conf80=len(good),
                   ocr_mean_conf=round(sum(c for _, c in words) / max(len(words), 1), 1), ocr_sample=" ".join(good[:80]))
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    return row

def nyt_checks():
    """Top Stories API shape + the NYT's own front-page PDF (today's and tomorrow's dated URLs)."""
    import datetime as dt, os
    row = {}
    key = os.environ.get("NYT_API_KEY", "")
    if key:
        try:
            r = S.get("https://api.nytimes.com/svc/topstories/v2/home.json", params={"api-key": key}, timeout=20)
            (RAW / "nyt-topstories.json.gz").write_bytes(gzip.compress(r.content.replace(key.encode(), b"KEY")))
            res = r.json().get("results", []) if r.ok else []
            row.update(api_status=r.status_code, api_items=len(res),
                       api_first=[(i.get("section"), i.get("item_type"), i.get("title")) for i in res[:6]],
                       api_media_widths=sorted({m.get("width") for i in res[:6] for m in i.get("multimedia") or []}))
        except Exception as e:
            row["api_error"] = f"{type(e).__name__}: {e}"
    else:
        row["api_error"] = "NYT_API_KEY not set (run with -e NYT_API_KEY=...)"
    for day in (dt.date.today(), dt.date.today() + dt.timedelta(days=1)):
        url = f"https://static01.nyt.com/images/{day:%Y/%m/%d}/nytfrontpage/scan.pdf"
        try:
            r = S.get(url, timeout=30)
            row[f"pdf_{day}"] = dict(status=r.status_code, bytes=len(r.content), modified=r.headers.get("Last-Modified"))
            if r.ok and r.content[:5] == b"%PDF-":
                (RAW / f"nyt-front-{day}.pdf").write_bytes(r.content)
                try:
                    import pypdfium2 as pdfium, io
                    pdf = pdfium.PdfDocument(r.content)
                    im = pdf[0].render(scale=200 / 72, grayscale=True).to_pil()
                    im.save(RAW / f"nyt-front-{day}.png")
                    import pytesseract
                    d = pytesseract.image_to_data(im, config="--psm 3", output_type=pytesseract.Output.DICT)
                    words = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
                    good = [w for w, c in words if c >= 80]
                    row[f"pdf_{day}"].update(size=im.size, ocr_words=len(words), ocr_words_conf80=len(good),
                                             ocr_mean_conf=round(sum(c for _, c in words) / max(len(words), 1), 1),
                                             ocr_sample=" ".join(good[:80]))
                except ImportError:
                    row[f"pdf_{day}"]["render"] = "pip install pypdfium2 to render"
        except Exception as e:
            row[f"pdf_{day}"] = {"error": f"{type(e).__name__}: {e}"}
    ff = f"https://cdn.freedomforum.org/dfp/pdf{dt.date.today().day}/NY_NYT.pdf"
    try:
        r = S.get(ff, timeout=30)
        row["freedomforum"] = dict(status=r.status_code, bytes=len(r.content), modified=r.headers.get("Last-Modified"))
    except Exception as e:
        row["freedomforum"] = {"error": f"{type(e).__name__}: {e}"}
    return row

def print_meta_checks():
    """Publisher print metadata + the BBC's nightly review (21 Oct design; all unverified until this runs)."""
    import datetime as dt, os
    today, row = dt.date.today(), {}
    gk = os.environ.get("GUARDIAN_API_KEY", "")
    for day in (today, today + dt.timedelta(days=1)):
        if not gk:
            row["guardian"] = "GUARDIAN_API_KEY not set (run with -e GUARDIAN_API_KEY=...)"; break
        try:
            r = S.get("https://content.guardianapis.com/search", timeout=20, params={
                "api-key": gk, "use-date": "newspaper-edition", "from-date": str(day), "to-date": str(day),
                "page-size": 50, "show-fields": "headline,newspaperPageNumber,newspaperEditionDate,wordcount,publication",
                "show-tags": "newspaper-book,newspaper-book-section"})
            (RAW / f"guardian-print-{day}.json.gz").write_bytes(gzip.compress(r.content.replace(gk.encode(), b"KEY")))
            res = r.json().get("response", {}).get("results", []) if r.ok else []
            p1 = [(i["fields"].get("headline"), i["fields"].get("wordcount"),
                   [t["id"] for t in i.get("tags", []) if t.get("type") == "newspaper-book"])
                  for i in res if str((i.get("fields") or {}).get("newspaperPageNumber")) == "1"]
            row[f"guardian_{day}"] = dict(status=r.status_code, results=len(res), page1=p1)
        except Exception as e:
            row[f"guardian_{day}"] = {"error": f"{type(e).__name__}: {e}"}
    nk = os.environ.get("NYT_API_KEY", "")
    if nk:
        try:
            time.sleep(13)                                   # NYT: 5 calls a minute
            variants = [None, 'print_page:1 AND print_section:"A"', 'print_page:"1"', "print_page:1",
                        'print_section:("A") AND print_page:("1")']
            for n, fq in enumerate(variants):
                params = {"api-key": nk, "begin_date": f"{today - dt.timedelta(days=2):%Y%m%d}", "end_date": f"{today:%Y%m%d}",
                          "sort": "newest"}
                if fq:
                    params["fq"] = fq
                r = S.get("https://api.nytimes.com/svc/search/v2/articlesearch.json", timeout=20, params=params)
                (RAW / f"nyt-search-{n}.json.gz").write_bytes(gzip.compress(r.content.replace(nk.encode(), b"KEY")))
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                resp = body.get("response") or {}
                docs = resp.get("docs") or []
                row[f"nyt_search_{n}"] = dict(fq=fq, status=r.status_code, hits=(resp.get("metadata") or resp.get("meta") or {}).get("hits"),
                                              fault=str(body.get("fault") or body.get("errors") or "")[:200],
                                              first=[(d.get("print_section"), d.get("print_page"),
                                                      (d.get("headline") or {}).get("print_headline"), d.get("pub_date"))
                                                     for d in docs[:6]],
                                              keys=sorted(docs[0].keys()) if docs else [])
                time.sleep(13)
        except Exception as e:
            row["nyt_a1"] = {"error": f"{type(e).__name__}: {e}"}
    code, html, final = get("https://www.bbc.co.uk/news/topics/cpml2v678pxt", "bbc-papers-topic")
    links = list(dict.fromkeys(up.urljoin(final, a["href"]) for a in BeautifulSoup(html or "", "lxml").find_all("a", href=True)
                               if "/news/articles/" in a["href"]))
    row["bbc_topic"] = dict(status=code, links=links[:4])
    for n, u in enumerate(links[:2]):
        c2, h2, _ = get(u, f"bbc-papers-article-{n}")
        soup = BeautifulSoup(h2 or "", "lxml")
        pub = re.search(r'"datePublished"\s*:\s*"([^"]+)"', h2 or "")
        figs = [(i.get("alt") or "")[:100] for i in soup.find_all("img")][:12]
        row[f"bbc_article_{n}"] = dict(url=u, status=c2, published=pub.group(1) if pub else None, img_alts=figs,
                                       paras=[p.get_text(" ", strip=True)[:160] for p in soup.find_all("p")][:16])
        time.sleep(1)
    return row

HONEST_UA = "papernews/1.0 (personal e-ink news display; RSS/XML reader; +https://github.com/njclarke1/trmnl-papernews)"
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"

def _try(url, tag, ua, accept=None):
    """One request with the given identity; never retried, never disguised."""
    try:
        h = {"User-Agent": ua, "Accept-Language": "en-GB,en;q=0.9"}
        if accept:
            h["Accept"] = accept
        r = requests.get(url, headers=h, timeout=20)
        (RAW / f"{tag}.gz").write_bytes(gzip.compress(r.content))
        txt = r.text
        titles = re.findall(r"<(?:news:)?title>(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?</(?:news:)?title>", txt)[:4]
        return {"status": r.status_code, "bytes": len(r.content), "ctype": r.headers.get("content-type", "")[:40],
                "items": txt.count("<item") + txt.count("<url>"), "titles": titles,
                "blocked_page": bool(re.search(r"access issue|verifying|unusual activity|captcha", txt[:3000], re.I))}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def alt_checks():
    """24 Oct handoff: are the Times/Telegraph reachable through public XML (feeds, news sitemaps,
    Google News) with an honest feed-reader identity? And does that identity keep the working sources?"""
    row = {}
    for name, ua in (("browser_ua", UA), ("honest_ua", HONEST_UA)):
        for tag, url in (("tg_rss", "https://www.telegraph.co.uk/rss.xml"),
                         ("tg_news_rss", "https://www.telegraph.co.uk/news/rss.xml"),
                         ("tg_robots", "https://www.telegraph.co.uk/robots.txt"),
                         ("tg_news_sitemap", "https://www.telegraph.co.uk/custom/daily-news/sitemap.xml"),
                         ("tm_robots", "https://www.thetimes.com/robots.txt")):
            row[f"{tag}/{name}"] = _try(url, f"alt-{tag}-{name}", ua, FEED_ACCEPT if "robots" not in tag else None)
            time.sleep(5)
    # the Times' news sitemap, if robots.txt declares one
    rb = gzip.decompress((RAW / "alt-tm_robots-honest_ua.gz").read_bytes()).decode("utf-8", "replace") \
        if (RAW / "alt-tm_robots-honest_ua.gz").exists() else ""
    maps = [l.split(":", 1)[1].strip() for l in rb.splitlines() if l.lower().startswith("sitemap:")]
    row["tm_sitemaps_declared"] = maps[:10]
    for n, m in enumerate([m for m in maps if "news" in m.lower()][:2]):
        row[f"tm_news_sitemap_{n}"] = {"url": m, **_try(m, f"alt-tm-news-sitemap-{n}", HONEST_UA, FEED_ACCEPT)}
        time.sleep(5)
    for tag, dom in (("gn_telegraph", "telegraph.co.uk"), ("gn_times", "thetimes.com")):
        row[tag] = _try(f"https://news.google.com/rss/search?q=when:24h+allinurl:{dom}&hl=en-GB&gl=GB&ceid=GB:en",
                        f"alt-{tag}", HONEST_UA, FEED_ACCEPT)
        time.sleep(5)
    row["openrss_times"] = _try("https://openrss.org/www.thetimes.com", "alt-openrss-times", HONEST_UA, FEED_ACCEPT)
    # would an honest identity break what works today?
    for tag, url in (("bbc_home", "https://www.bbc.co.uk/news"), ("spectator_home", "https://spectator.com"),
                     ("i_home", "https://inews.co.uk"), ("newworld_home", "https://www.thenewworld.co.uk"),
                     ("ft_rss", "https://www.ft.com/rss/home/uk"), ("spectator_feed", "https://spectator.com/feed/")):
        r = _try(url, f"alt-{tag}-honest", HONEST_UA, FEED_ACCEPT if ("rss" in url or "feed" in url) else None)
        row[f"{tag}/honest_ua"] = {k: r.get(k) for k in ("status", "bytes", "items", "blocked_page", "error")}
        time.sleep(3)
    return row

if not sys.argv[1:] or "alt" in sys.argv[1:]:
    results["_alt"] = alt_checks()

if not sys.argv[1:] or "nyt" in sys.argv[1:]:
    results["_nyt"] = nyt_checks()

if not sys.argv[1:] or "print" in sys.argv[1:]:
    results["_print_meta"] = print_meta_checks()

if not sys.argv[1:] or "fronts" in sys.argv[1:]:
    results["_front_images"] = {k: front_image(k, s) for k, s in FRONTS.items()}
    results["_tomorrowspapers"] = tp_scan()

def redact(t):
    """API keys can appear in error messages (requests echoes the URL); never let them into the outputs."""
    import os
    for k in ("GUARDIAN_API_KEY", "NYT_API_KEY"):
        v = os.environ.get(k, "")
        if len(v) > 6:
            t = t.replace(v, "KEY")
    return t

(OUT / "results.json").write_text(redact(json.dumps(results, indent=1, default=str)))

def yn(b): return "✅" if b else "❌"
lines = ["# Front-page body probe", f"_Run {time.strftime('%Y-%m-%d %H:%M')}_", "",
         "| Paper | Home | Lead links | RSS | Lead article | Free flag | Body JSON-LD | Body extracted | Image |",
         "|---|---|---|---|---|---|---|---|---|"]
for key, r in results.items():
    if key.startswith("_"): continue
    a = (r["articles"] or [{}])[0]
    lines.append(f"| {key} | {r['home_status']} | {len(r['home_links'])} | {r['rss_status']} ({len(r['rss_items'])}) | "
                 f"{a.get('status')} | {a.get('isAccessibleForFree')}{' (gated part)' if a.get('gated_part') else ''} | "
                 f"{a.get('body_jsonld_chars', 0)} | {a.get('body_trafilatura_chars', 0)} | {yn(a.get('image'))} |")
lines += ["", "Body ≥ 1500 chars on a free-flagged article ⇒ full layout. Free flag false ⇒ summary layout.", ""]
for key, r in results.items():
    if key.startswith("_"): continue
    lines.append(f"## {key}\n\n**Homepage order (first 5):**")
    lines += [f"{i+1}. {l['text'] or '(no text)'} — `{l['url']}`" for i, l in enumerate(r["home_links"][:5])]
    lines.append("\n**RSS first 3:** " + " · ".join(i["title"] for i in r["rss_items"][:3]))
    for a in r["articles"]:
        lines.append(f"\n- `{a.get('url')}` → {a.get('status')}, free={a.get('isAccessibleForFree')}, "
                     f"jsonld={a.get('body_jsonld_chars')} chars, extracted={a.get('body_trafilatura_chars')} chars, "
                     f"section={a.get('section')}, author={a.get('author')}, markers={a.get('paywall_markers')}\n  > {a.get('body_sample','')[:200]}")
    lines.append("")
fi = results.get("_front_images")
if fi:
    lines += ["## Published front-page images (OCR legibility)", "",
              "| Paper | Image size | OCR words | ≥80% conf | Mean conf |", "|---|---|---|---|---|"]
    lines += [f"| {k} | {v.get('size')} | {v.get('ocr_words')} | {v.get('ocr_words_conf80')} | {v.get('ocr_mean_conf')} |"
              for k, v in fi.items()]
    lines += ["", "Rule of thumb (synthetic broadsheet test, 21 Oct): body text OCRs reliably at ≥1200 px wide (98–100% word recall),"
              " is marginal at 1000 px (92–99%) and fails at 800 px. Real fronts also need ≥150 words at ≥80% confidence.", ""]
    lines += [f"**{k} sample:** {v.get('ocr_sample','')[:400]}" for k, v in fi.items()] + [""]
al = results.get("_alt")
if al:
    lines += ["## Alternative public XML routes (Times, Telegraph) and an honest user agent", "", "```",
              json.dumps(al, indent=1, default=str)[:6000], "```", ""]
tp = results.get("_tomorrowspapers")
if tp:
    lines += ["## tomorrowspapers.co.uk editions (masthead OCR)", "",
              f"index {tp.get('index_status')}, {tp.get('entries')} entries, identified: {tp.get('identified')}", "",
              "| Entry | Size | Identified | Masthead text |", "|---|---|---|---|"]
    lines += [f"| {r.get('entry','').rsplit('/', 2)[-2]} | {r.get('size')} | {r.get('identified')} | {(r.get('masthead_text') or r.get('error') or '')[:90]} |"
              for r in tp.get("rows", [])]
    lines += [""] + [f"**{r['identified']} OCR:** {json.dumps(r['ocr'])[:600]}" for r in tp.get("rows", []) if r.get("ocr")] + [""]
ny = results.get("_nyt")
if ny:
    lines += ["## The New York Times (API + printed-front PDF)", "", "```", json.dumps(ny, indent=1, default=str)[:3000], "```", ""]
pm = results.get("_print_meta")
if pm:
    lines += ["## Print metadata (Guardian page 1, NYT A1) and the BBC review", "", "```", json.dumps(pm, indent=1, default=str)[:5000], "```", ""]
pr = results.get("_papers_review")
if pr: lines += ["## PA 'What the papers say'", f"index {pr['index_status']}, links {pr['links'][:2]}", ""]
(OUT / "report.md").write_text(redact("\n".join(lines)))
print("\n".join(lines[:12]))
