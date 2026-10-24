"""Render every demo state through the real template to 16-grey PNGs (dev only; no device needed).

Runs the sidecar in DEMO_MODE on 127.0.0.1:3499, renders plugin/full.liquid with python-liquid for
each /demo/<state>.json, screenshots at 1236×1648 in Chromium and quantises to the Kindle's 16 greys.

    python tools/render_harness.py [state ...]      # → out/harness/<state>.png + report
"""
import json
import os
import pathlib
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 3499
os.environ.update(DEMO_MODE="1", FRONTPAGE_NO_BACKGROUND="1", PUBLIC_BASE_URL=f"http://127.0.0.1:{PORT}",
                  DATA_DIR=os.environ.get("DATA_DIR") or tempfile.mkdtemp(prefix="fp-harness-"))

import numpy as np                                   # noqa: E402
from liquid import Environment                       # noqa: E402
from PIL import Image                                # noqa: E402
from playwright.sync_api import sync_playwright      # noqa: E402
from werkzeug.serving import make_server             # noqa: E402

from app.main import create_app                      # noqa: E402
from app.sources import demo                         # noqa: E402

OUT = ROOT / "out" / "harness"


def main(states):
    OUT.mkdir(parents=True, exist_ok=True)
    app = create_app(background=False)
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tpl = Environment().from_string((ROOT / "plugin/full.liquid").read_text())
    client = app.test_client()
    report = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1236, "height": 1648})
        for st in states:
            data = client.get(f"/demo/{st}.json").get_json()
            # wrapped exactly as LaraPaper serves it to the PW11 preset (measured on the device, 24 Oct 2026)
            html = ("<!doctype html><html><head><meta charset='utf-8'><style>.screen{width:800px;height:600px;"
                    "transform:matrix(2.06,0,0,2.06,0,0);transform-origin:0 0}</style></head>"
                    "<body class='environment trmnl'><div class='screen screen--amazon_kindle_paperwhite_signature_11th_gen "
                    f"screen--4bit screen--scale-xlarge'>{tpl.render(**data)}</div></body></html>")
            (OUT / f"{st}.html").write_text(html)
            page.goto(f"http://127.0.0.1:{PORT}/healthz")          # same origin for fonts/images
            page.set_content(html, wait_until="networkidle")
            page.wait_for_selector("body[data-ready]", state="attached", timeout=15000)
            raw = OUT / f"{st}-raw.png"
            page.screenshot(path=str(raw), clip={"x": 0, "y": 0, "width": 1236, "height": 1648})
            g = np.asarray(Image.open(raw).convert("L"), float)
            Image.fromarray((np.round(g / 255 * 15) * 17).astype("uint8")).save(OUT / f"{st}.png", dpi=(300, 300))
            raw.unlink()
            report[st] = page.evaluate("""() => {
              const fp=document.querySelector('.fp'), r=fp.getBoundingClientRect();
              const box=[r.left,r.top,r.width,r.height].map(Math.round).join(',');
              const spill=[...fp.querySelectorAll('*')].filter(e=>e.offsetParent&&e.getBoundingClientRect().bottom>r.bottom-24+1&&!e.closest('.foot')).length;
              const cut=[...fp.querySelectorAll('.flow,.list,.col')].some(x=>x.scrollHeight>x.clientHeight+1);
              const hed=fp.querySelector('.hed'); const fonts=[...document.fonts].filter(f=>f.status==='loaded').length;
              return {layout:%s, box, spill, cut, hed: hed?getComputedStyle(hed).fontSize:null, fonts_loaded:fonts,
                      jump: !!fp.querySelector('.jump') && getComputedStyle(fp.querySelector('.jump')).visibility};
            }""" % json.dumps(data["layout"]))
            print(st, report[st], flush=True)
        b.close()
    srv.shutdown()
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    main(sys.argv[1:] or list(demo.states()))
