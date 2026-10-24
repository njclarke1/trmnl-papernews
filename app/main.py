"""HTTP API polled by the LaraPaper plugin.

GET /edition              the paper for the current rotation slot (current locked edition)
GET /edition/<paper>      a specific paper
GET /demo/<state>.json    fixed demo states (per paper, plus stress cases) for checking the panel
GET /healthz              edition build status (also runs a build tick)
GET /static/fonts/<f>     OFL fonts for the template
GET /img/<f>, /qr/<f>     processed photos and QR codes from the data volume
"""
from __future__ import annotations

import logging
import os
import pathlib

from flask import Flask, abort, jsonify, send_from_directory

from . import clock, config, layout, payload, weather
from .builder import Builder, run_background
from .papers import PAPERS
from .sources import demo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC = pathlib.Path(__file__).parent / "static"


def create_app(builder: Builder | None = None, background: bool = True) -> Flask:
    app = Flask(__name__)
    b = builder or Builder()
    if background and os.environ.get("FRONTPAGE_NO_BACKGROUND") != "1":
        run_background(b)

    def _payload(paper: str | None):
        at = clock.now()
        slot, ed, preparing = b.current(at, paper)
        return payload.build(ed, slot, weather=weather.current(), preparing=preparing)

    @app.get("/edition")
    def edition():
        return jsonify(_payload(None))

    @app.get("/edition/<paper>")
    def edition_paper(paper: str):
        if paper not in PAPERS:
            abort(404)
        return jsonify(_payload(paper))

    @app.get("/demo/<state>.json")
    def demo_state(state: str):
        states = demo.states()
        if state not in states:
            abort(404)
        ed = states[state]
        ed.layout = ed.layout or layout.choose(ed, PAPERS[ed.paper])
        ed.image = b.build_image(ed)
        slot = clock.edition_for(clock.now())
        w = {"label": config.WEATHER_LABEL, "temp": "12°", "hi": "14°", "lo": "6°", "icon": "partly", "night": False,
             "text": "Partly cloudy", "glyph": "partly"}
        return jsonify(payload.build(ed, slot, weather=w))

    @app.get("/healthz")
    def healthz():
        status = b.status()
        return jsonify({"ok": True, "now": clock.now().isoformat(timespec="seconds"),
                        "edition": clock.edition_for(clock.now()).id, **status})

    @app.get("/static/fonts/<path:name>")
    def fonts(name):
        # LaraPaper renders the markup from its own origin (setContent), so web fonts are cross-origin
        # and the browser refuses them without CORS (images don't need it). First live render,
        # 24 Oct 2026: every face fell back to Noto.
        r = send_from_directory(STATIC / "fonts", name, max_age=86400 * 30)
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    @app.after_request
    def cors(resp):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")   # read-only, LAN-only public data
        return resp

    @app.get("/img/<name>")
    def img(name):
        return send_from_directory(pathlib.Path(config.DATA_DIR) / "img", name, max_age=86400)

    @app.get("/qr/<name>")
    def qr(name):
        return send_from_directory(pathlib.Path(config.DATA_DIR) / "qr", name, max_age=86400)

    return app
