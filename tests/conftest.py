import os
import sys
import pathlib
import tempfile

# environment before any app import (config is read once, at import)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="fp-test-"))
os.environ.setdefault("FRONTPAGE_NO_BACKGROUND", "1")
os.environ.setdefault("PUBLIC_BASE_URL", "http://test:3461")
os.environ.setdefault("HOST_PACING_S", "0")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.sources.base import Http, SourceUnavailable  # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network_weather(monkeypatch):
    from app import weather
    monkeypatch.setattr(weather, "current", lambda session=None: {
        "label": "Bristol", "temp": "12°", "hi": "14°", "lo": "6°", "icon": "rain", "night": False,
        "text": "Rain", "glyph": "rain"})


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "PIN", "")
    return tmp_path


class Resp:
    def __init__(self, url, text="", content=None, status=200, json_data=None, headers=None):
        self.url, self.text, self.status_code = url, text, status
        self.headers = headers or {}
        self.content = content if content is not None else text.encode()
        self._json = json_data

    def close(self):
        pass

    def json(self):
        import json
        return self._json if self._json is not None else json.loads(self.text)


class FakeHttp(Http):
    """Map URL prefixes to responses; anything unmapped is an outage."""

    def __init__(self, routes: dict):
        super().__init__()
        self.routes, self.calls = routes, []

    def get(self, url, *, binary=False, **kw):
        self.calls.append(url)
        for prefix, r in self.routes.items():
            if url.startswith(prefix):
                if isinstance(r, Exception):
                    raise r
                return r if isinstance(r, Resp) else Resp(url, r)
        raise SourceUnavailable(f"unmapped {url}")


@pytest.fixture
def fake_http():
    return FakeHttp
