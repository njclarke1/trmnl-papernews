"""Simplest weather (decided 21 Oct): one keyless Open-Meteo call, cached for an hour.
The only element that changes between editions."""
from __future__ import annotations

import time

import requests

from . import config

_CACHE: dict = {"at": 0.0, "data": None}

# WMO weather codes → icon + short text
_CODES = [
    ((0,), "clear", "Clear"), ((1, 2), "partly", "Partly cloudy"), ((3,), "cloud", "Cloudy"),
    ((45, 48), "fog", "Fog"), ((51, 53, 55, 56, 57), "rain", "Drizzle"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "rain", "Rain"), ((71, 73, 75, 77, 85, 86), "snow", "Snow"),
    ((95, 96, 99), "storm", "Thunder"),
]


def describe(code: int) -> tuple[str, str]:
    for codes, icon, text in _CODES:
        if code in codes:
            return icon, text
    return "cloud", "Cloudy"


def glyph(icon: str, night: bool) -> str:
    """The template's icon: a moon replaces the sun at night."""
    return "moon" if night and icon in ("clear", "partly") else icon


def cached() -> dict | None:
    """What a request uses: never touches the network (LaraPaper times out polls at 15 s)."""
    return _CACHE["data"]


def current(session: requests.Session | None = None) -> dict | None:
    """Refresh at most hourly. Called from the background builder, not from requests."""
    if _CACHE["data"] and time.time() - _CACHE["at"] < 3600:
        return _CACHE["data"]
    try:
        s = session or requests
        r = s.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
            "latitude": config.WEATHER_LAT, "longitude": config.WEATHER_LON,
            "current": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min", "timezone": config.TZ, "forecast_days": 1})
        r.raise_for_status()
        j = r.json()
        icon, text = describe(int(j["current"]["weather_code"]))
        data = {"label": config.WEATHER_LABEL, "temp": f"{round(j['current']['temperature_2m'])}°",
                "hi": f"{round(j['daily']['temperature_2m_max'][0])}°", "lo": f"{round(j['daily']['temperature_2m_min'][0])}°",
                "icon": icon, "night": not j["current"].get("is_day", 1), "text": text}
        data["glyph"] = glyph(icon, data["night"])
        _CACHE.update(at=time.time(), data=data)
        return data
    except Exception:
        return _CACHE["data"]          # stale is better than blank; None hides the ear's weather
