"""
OpenWeatherMap client. Cached ~20min per ~1km grid cell per
CLAUDE(CARA-BACKEND).md's "cache 15-30 min per area, don't call per-request".
Weather is a soft context signal (not core plumbing) - on any failure this
falls back to a neutral default rather than failing the whole
/recommendations request.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings

OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
CACHE_TTL_SECONDS = 20 * 60
EXTREME_HEAT_THRESHOLD_C = 40.0

_CONDITION_MAP = {
    "Clear": "clear",
    "Clouds": "cloudy",
    "Rain": "rain",
    "Drizzle": "rain",
    "Thunderstorm": "rain",
    "Mist": "cloudy",
    "Haze": "cloudy",
    "Fog": "cloudy",
    "Smoke": "cloudy",
    "Dust": "cloudy",
    "Snow": "cloudy",
}

_FALLBACK = {"weather_condition": "clear", "temp_celsius": 28.0}

# (rounded_lat, rounded_lon) -> (fetched_at_monotonic, result)
_cache: dict[tuple[float, float], tuple[float, dict]] = {}


def get_weather(lat: float, lon: float) -> dict:
    cache_key = (round(lat, 2), round(lon, 2))
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    params = {"lat": lat, "lon": lon, "appid": settings.openweather_api_key, "units": "metric"}
    url = f"{OPENWEATHER_URL}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        temp_celsius = float(data["main"]["temp"])
        main_condition = data["weather"][0]["main"] if data.get("weather") else "Clear"
        weather_condition = _CONDITION_MAP.get(main_condition, "cloudy")
        if temp_celsius >= EXTREME_HEAT_THRESHOLD_C:
            weather_condition = "extreme_heat"
        result = {"weather_condition": weather_condition, "temp_celsius": temp_celsius}
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, ValueError):
        result = _FALLBACK

    _cache[cache_key] = (now, result)
    return result
