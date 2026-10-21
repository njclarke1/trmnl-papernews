"""All settings come from the environment (Portainer stack env). Read once at import."""
import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _bool(key: str, default: bool = False) -> bool:
    v = _env(key, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "on")


TZ = "Europe/London"
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "http://192.168.68.111:3461").rstrip("/")
DATA_DIR = _env("DATA_DIR", "/data")
PLACE = _env("PLACE", "Bristol")

# Editions (decided 21 Oct 2026): locked morning + evening editions, no live updating
AM_LOCK = _env("AM_LOCK", "06:00")
PM_LOCK = _env("PM_LOCK", "16:00")
RETRY_WINDOW_MIN = int(_env("RETRY_WINDOW_MIN", "60"))
RETRY_EVERY_MIN = int(_env("RETRY_EVERY_MIN", "5"))

# Rotation: equal weights; one paper per slot
from .papers import DEFAULT_ROTATION, PAPERS  # noqa: E402

ROTATION = [p.strip() for p in _env("ROTATION", DEFAULT_ROTATION).split(",") if p.strip() in PAPERS]
# minutes per paper; 2–5 intended. Content only changes at the two edition locks, so a short slot
# just cycles through papers faster. Match the Kindle's refresh interval.
SLOT_MINUTES = max(1, int(_env("SLOT_MINUTES", "3")))
PIN = _env("PIN")                        # lock the display to one paper

# Sources
GUARDIAN_API_KEY = _env("GUARDIAN_API_KEY")
NYT_API_KEY = _env("NYT_API_KEY")          # free key from developer.nytimes.com (Top Stories API)
USER_AGENT = _env("USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36")
HOST_PACING_S = float(_env("HOST_PACING_S", "2"))
MIN_BODY_CHARS = int(_env("MIN_BODY_CHARS", "1500"))
OCR_ENABLED = _bool("OCR_ENABLED", True)
DEMO_MODE = _bool("DEMO_MODE", False)    # serve fixture editions instead of fetching (dev / harness)

# Weather: simplest possible — one keyless Open-Meteo call, hourly
WEATHER_LAT = float(_env("WEATHER_LAT", "51.4545"))
WEATHER_LON = float(_env("WEATHER_LON", "-2.5879"))
WEATHER_LABEL = _env("WEATHER_LABEL", "Bristol")

# Look
IMAGE_MODE = _env("IMAGE_MODE", "halftone")          # halftone | clean
FT_TINT = _bool("FT_TINT", False)                    # light grey page for the FT, a nod to the pink paper
