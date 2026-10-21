"""Phase 1 probe: can each paper supply Guardian-level content (ranked lead + body + image)?

Run on the NAS (residential IP; the dev sandbox cannot reach these sites):
  docker run --rm -v ~/fp-probe:/w -w /w python:3.12-slim sh -c \
    "apt-get -qq update && apt-get -qq install -y tesseract-ocr >/dev/null && \
     pip -q install requests beautifulsoup4 lxml trafilatura pillow pytesseract pypdfium2 && python probe_bodies.py"
  (front-page images only: append `fronts`; NYT API + PDF: `nyt`; print metadata + BBC review: `print`;
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
  "spectator": dict(home="https://www.spectator.co.uk",
                    rss=["https://www.spectator.co.uk/feed/"],
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

# ---- published front-page images: resolution + OCR legibility (frontpages.com og:image) ----
FRONTS = {"ft": "financial-times", "guardian": "the-guardian", "telegraph": "the-daily-telegraph", "i": "i-newspaper"}
def front_image(key, slug):
    code, html, final = get(f"https://www.frontpages.com/{slug}", f"{key}-front-page")
    row = {"page_status": code}
    if code != 200:
        return row
    og = BeautifulSoup(html, "lxml").find("meta", property="og:image")
    row["image_url"] = og.get("content") if og else None
    if not row["image_url"]:
        return row
    try:
        r = S.get(row["image_url"], timeout=30); (RAW / f"{key}-front.jpg").write_bytes(r.content)
        from PIL import Image; import io
        im = Image.open(io.BytesIO(r.content)).convert("L"); row["size"] = im.size
        try:
            import pytesseract
            big = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)      # upscale helps small type
            d = pytesseract.image_to_data(big, config="--psm 3", output_type=pytesseract.Output.DICT)
            words = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
            good = [w for w, c in words if c >= 80]
            row.update(ocr_words=len(words), ocr_words_conf80=len(good),
                       ocr_mean_conf=round(sum(c for _, c in words) / max(len(words), 1), 1),
                       ocr_sample=" ".join(good[:80]))
        except Exception as e:
            row["ocr_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        row["image_error"] = f"{type(e).__name__}: {e}"
    return row

def front_image_tp(key, title_re):
    """The Times isn't on frontpages.com: look through tomorrowspapers.co.uk's nightly entries for it."""
    code, html, final = get("https://www.tomorrowspapers.co.uk/", f"{key}-tp-index")
    if code != 200:
        return {"page_status": code}
    entries = [up.urljoin(final, a["href"]) for a in BeautifulSoup(html, "lxml").find_all("a", href=True)
               if re.search(r"/\d{4}-\d{2}-\d{2}-[0-9a-f]{6}/?$", a["href"])]
    for n, u in enumerate(dict.fromkeys(entries[:30])):
        c, h, _ = get(u, f"{key}-tp-{n}")
        if c != 200: continue
        soup = BeautifulSoup(h, "lxml"); title = (soup.title.string if soup.title else "") or ""
        if re.search(title_re, title, re.I):
            og = soup.find("meta", property="og:image")
            imgs = [i.get("src") for i in soup.find_all("img") if i.get("src") and "uploads" in i.get("src")]
            url = (og.get("content") if og else None) or (imgs[0] if imgs else None)
            row = {"page_status": c, "entry": u, "title": title.strip()[:120]}
            if url:
                row.update(_ocr_url(key, url))
            return row
        time.sleep(1)
    return {"page_status": code, "note": f"no entry matching {title_re} in first 30"}

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
            r = S.get("https://api.nytimes.com/svc/search/v2/articlesearch.json", timeout=20, params={
                "api-key": nk, "begin_date": f"{today - dt.timedelta(days=1):%Y%m%d}", "end_date": f"{today:%Y%m%d}",
                "fq": 'print_page:1 AND print_section:"A"', "sort": "newest"})
            (RAW / "nyt-a1.json.gz").write_bytes(gzip.compress(r.content.replace(nk.encode(), b"KEY")))
            docs = ((r.json().get("response") or {}).get("docs") or []) if r.ok else []
            row["nyt_a1"] = dict(status=r.status_code, docs=len(docs), first=[
                (d.get("print_section"), d.get("print_page"), (d.get("headline") or {}).get("print_headline"),
                 d.get("pub_date"), type(d.get("multimedia")).__name__) for d in docs[:8]])
        except Exception as e:
            row["nyt_a1"] = {"error": f"{type(e).__name__}: {e}"}
    for idx in ("https://www.bbc.co.uk/news/blogs/the_papers", "https://www.bbc.co.uk/news/uk"):
        code, html, final = get(idx, "bbc-papers-index-" + idx.rsplit("/", 1)[-1])
        links = [(a.get_text(" ", strip=True)[:90], a["href"]) for a in BeautifulSoup(html or "", "lxml").find_all("a", href=True)
                 if re.match(r"\s*(newspaper headlines|the papers)", a.get_text(" "), re.I)]
        row[f"bbc_{idx.rsplit('/', 1)[-1]}"] = dict(status=code, final=final, links=links[:5])
        if links and code == 200:
            u = up.urljoin(final, links[0][1]); c2, h2, _ = get(u, "bbc-papers-article")
            row["bbc_article"] = dict(url=u, status=c2, sample=re.sub(r"\s+", " ", BeautifulSoup(h2 or "", "lxml").get_text(" "))[:1200])
            break
    return row

if not sys.argv[1:] or "nyt" in sys.argv[1:]:
    results["_nyt"] = nyt_checks()

if not sys.argv[1:] or "print" in sys.argv[1:]:
    results["_print_meta"] = print_meta_checks()

if not sys.argv[1:] or "fronts" in sys.argv[1:]:
    results["_front_images"] = {k: front_image(k, s) for k, s in FRONTS.items()}
    results["_front_images"]["times"] = front_image_tp("times", r"\bthe times\b(?!.*(financial|sunday|new york|india))")

(OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))

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
ny = results.get("_nyt")
if ny:
    lines += ["## The New York Times (API + printed-front PDF)", "", "```", json.dumps(ny, indent=1, default=str)[:3000], "```", ""]
pm = results.get("_print_meta")
if pm:
    lines += ["## Print metadata (Guardian page 1, NYT A1) and the BBC review", "", "```", json.dumps(pm, indent=1, default=str)[:5000], "```", ""]
pr = results.get("_papers_review")
if pr: lines += ["## PA 'What the papers say'", f"index {pr['index_status']}, links {pr['links'][:2]}", ""]
(OUT / "report.md").write_text("\n".join(lines))
print("\n".join(lines[:12]))
