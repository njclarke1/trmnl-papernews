# trmnl-frontpage

A newspaper front page for a jailbroken Kindle Paperwhite (11th gen, 1236×1648, 16 greys) running TRMNL through KOReader, served by a self-hosted LaraPaper.

Seven papers rotate one per refresh: **The Guardian, BBC News, Financial Times, The Times, The Independent, i and The Daily Telegraph**. Each is shown as that paper's own front: its lead story and its other top stories, under its own nameplate.

Two **locked editions** a day: **morning (06:00)** and **evening (16:00)**. Nothing changes between locks except the weather. Overnight, the evening edition stays up until the morning one.

Flask sidecar (`app/`) → JSON → LaraPaper polling plugin (`plugin/full.liquid`) → Kindle.

## Where the content comes from

| Paper | Order (the "front") | Body text |
|---|---|---|
| Guardian | Content API editors' picks | Content API |
| BBC, Independent, i, Telegraph | Homepage order, found by article URL pattern; RSS order as fallback | The article page a normal visitor gets (JSON-LD `articleBody`, then trafilatura) |
| FT, The Times | Homepage headlines | **Morning edition:** OCR of the published printed front page (opening of the splash, up to "Continued on page N"). Otherwise headline and standfirst only |

- **Morning edition:** PA's nightly "What the papers say" round-up identifies each paper's **printed splash**, which is matched to that paper's web article (`print_front`, labelled "Today's front page").
- **The publisher's own free flag rules:** if an article is marked paid (`isAccessibleForFree: false`, or a gated `hasPart`), no body text is shown, whatever the extractor found. There is no paywall circumvention of any kind.
- **Layouts are chosen from what actually arrived:** `full` (body text), `summary` (photo + standfirst), `poster`, `bulletin` (list with summaries) or `briefing` (list only). A paper never shows a broken page; it steps down a layout.

## Endpoints

| Path | |
|---|---|
| `/edition` | The paper for the current rotation slot, from the current locked edition |
| `/edition/<paper>` | A specific paper: `guardian bbc ft times indy i telegraph` |
| `/demo/<state>.json` | Fixed demo states: one per paper, plus `ft-print`, `long-headline`, `no-image`, `cached` |
| `/healthz` | Build status per paper for the current edition; also runs a build tick |
| `/static/fonts/*`, `/img/*`, `/qr/*` | Fonts, processed photos and QR codes for the template |

## Settings (Portainer stack env)

| Var | Default | |
|---|---|---|
| `GUARDIAN_API_KEY` | — | Free Developer key from open-platform.theguardian.com (non-commercial) |
| `PUBLIC_BASE_URL` | `http://192.168.68.111:3461` | What the template uses to load fonts and images |
| `ROTATION` | `guardian,bbc,ft,times,indy,i,telegraph` | Equal weights; list a paper twice to weight it |
| `SLOT_MINUTES` | `20` | Minutes per paper; match the Kindle refresh interval |
| `AM_LOCK` / `PM_LOCK` | `06:00` / `16:00` | Europe/London wall-clock time |
| `PIN` | — | Show one paper only |
| `WEATHER_LAT/LON/LABEL` | Bristol | One keyless Open-Meteo call an hour |
| `IMAGE_MODE` | `halftone` | `halftone` (newsprint dots) or `clean` (Floyd–Steinberg 16 greys) |
| `FT_TINT` | `0` | `1` gives the FT a light grey page, a nod to the pink paper |
| `OCR_ENABLED` | `1` | Printed-front OCR for the FT and The Times |
| `DEMO_MODE` | `0` | Serve demo editions instead of fetching |

`RETRY_WINDOW_MIN` (60) and `RETRY_EVERY_MIN` (5) control edition retries. `HOST_PACING_S` (2) sets the minimum gap between requests to one site. `MIN_BODY_CHARS` (1500) is the body length needed for the full layout.

## Deploy

1. Portainer → Stacks → Add stack → Repository: this repo, compose path `docker-compose.yml`. Set the env vars above.
2. Redeploy with **Re-pull image OFF**. Never run `docker compose` by hand in Portainer's compose directory.
3. Check: `curl http://192.168.68.111:3461/healthz`. After the next lock, every paper should show `ok`, `carried` or `missing`.
4. LaraPaper: create plugin `trmnl-frontpage`, polling URL `http://192.168.68.111:3461/edition`, and paste `plugin/full.liquid` as its markup.
   - **Re-paste after every change to `full.liquid`**; LaraPaper doesn't pull it from GitHub.
5. The Kindle must first render at native resolution: see `docs/phase0-kindle.md`.

To check a state on the panel without waiting for real news, point a second plugin's polling URL at `/demo/<state>.json`.

## Development

```bash
pip install -r requirements-dev.txt && playwright install chromium   # plus: apt install tesseract-ocr
pytest -q                                   # 64 tests; OCR tests skip without tesseract
python tools/render_harness.py              # every demo state → out/harness/*.png (16 greys) + report.json
python tools/make_synthetic_fronts.py       # regenerate OCR test fronts
```

The harness renders the **real** `full.liquid` with python-liquid in Chromium at 1236×1648. Its report flags spill (anything past the page), clipped text boxes, headline size and fonts loaded.

## Known limitations (read before trusting it)

- **Unverified against the live sites.** Homepage URL patterns, RSS feeds, JSON-LD shapes, the Guardian API's editors' picks, the PA round-up's format and the front-page image sources all come from desk research and synthetic tests. Phase 1 (`tools/probe_bodies.py` in the project docs) must run on the NAS, and adapters must be adjusted from its saved responses.
- **OCR is tuned on synthetic fronts.** It reached 94–100% recall with no leakage from other stories on test pages. Real fronts (busier layouts, adverts, wraps, odd splash shapes) will need tuning against real images. Every failure falls back to the summary layout, never to garbled text.
- **Front-page image sizes are unknown.** Below about 1,000 px wide, OCR is refused. The Times isn't on frontpages.com; it's looked for on tomorrowspapers.co.uk.
- **Paywalled papers** (FT, Times, and paid Independent/Telegraph pieces) show headline and standfirst only, except the morning OCR of the printed front.
- **Nameplates are typeset stand-ins.** Real wordmarks (to be cached on the volume, gitignored) are not wired up yet.
- **Live blogs** are shown as summary layouts; there's no key-events view yet.
- **LaraPaper's renderer** must support `initial-letter`, `text-wrap: balance`, `text-align-last`, `document.fonts.load` and fonts loaded from the sidecar. The harness proves Chromium 141 does; LaraPaper's Puppeteer version is unverified.
- **The PA round-up** is a third-party syndication; if it disappears, morning editions use homepage order (`web_front`).

## Licences

Fonts in `app/static/fonts` are SIL Open Font Licence 1.1 (licence files alongside): Playfair Display, Source Serif 4, Libre Franklin and UnifrakturMaguntia. Demo photos in `app/demo_photos` are from the scikit-image/imageio sample data (public domain / CC0). News content is fetched at run time and never stored in this repository.
