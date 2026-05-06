#!/usr/bin/env python3
"""
Weather Tool - Open-Meteo free weather API (no API key required)

Uses Open-Meteo (https://api.open-meteo.com/) which provides free weather
data without authentication. Falls back gracefully if network is unavailable.
"""

import json
import logging
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)

# Macau coordinates
DEFAULT_LAT = 22.1987
DEFAULT_LON = 113.5439

WEATHER_CODES = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多雲",
    3: "陰天",
    45: "有霧",
    48: "霧凇",
    51: "輕微細雨",
    53: "中等細雨",
    55: "密集細雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "局部陣雨",
    81: "中等陣雨",
    82: "強烈陣雨",
    85: "局部雪陣",
    86: "強雪陣",
    95: "雷暴",
    96: "雷暴伴輕冰雹",
    99: "雷暴伴重冰雹",
}


def get_weather(location: str = None, latitude: float = None, longitude: float = None, task_id: str = None) -> str:
    """
    Get current weather for a location. Uses Macau as default if no location specified.
    Open-Meteo is a free API (no API key needed).
    """
    lat = latitude if latitude is not None else DEFAULT_LAT
    lon = longitude if longitude is not None else DEFAULT_LON

    # Build Open-Meteo URL
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "timezone": "Asia/Macau",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"無法取得天氣資料: {str(e)}",
        })

    cw = data.get("current_weather", {})
    temp = cw.get("temperature", "N/A")
    windspeed = cw.get("windspeed", "N/A")
    winddirection = cw.get("winddirection", "N/A")
    weathercode = cw.get("weathercode", 0)
    is_day = cw.get("is_day", 1)
    time = cw.get("time", "N/A")

    weather_desc = WEATHER_CODES.get(weathercode, f"代碼{weathercode}")

    # Wind direction text
    directions = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]
    wd_text = directions[int((winddirection + 22.5) / 45) % 8] if isinstance(winddirection, (int, float)) else "N/A"

    location_name = location if location else "澳門"

    result = {
        "success": True,
        "location": location_name,
        "time": time,
        "temperature_c": temp,
        "weather": weather_desc,
        "wind_speed_kmh": windspeed,
        "wind_direction": wd_text,
        "is_day": bool(is_day),
        "raw": cw,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


WEATHER_SCHEMA = {
    "name": "get_weather",
    "description": """Get current weather for a location. Uses Macau as default.
    
Examples:
- "澳門今日天氣" -> get_weather(location="澳門")
- "澳門現在冷不冷?" -> get_weather(location="澳門")
- "台北天氣" -> get_weather(location="台北") (uses Macau coords as fallback since this tool uses Open-Meteo which supports global locations but requires explicit lat/lon)

Returns: temperature (°C), weather description (sunny/cloudy/rainy/etc), wind speed and direction.""",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "Location name (e.g. '澳門', 'Macau'). Note: this tool uses Open-Meteo which provides global data but requires coordinates. Currently uses Macau coordinates as default."
            },
            "latitude": {
                "type": "number",
                "description": "Latitude of the location (e.g. 22.1987 for Macau)."
            },
            "longitude": {
                "type": "number",
                "description": "Longitude of the location (e.g. 113.5439 for Macau)."
            },
        },
        "properties_order": ["location", "latitude", "longitude"],
    },
}


def check_weather_requirements() -> bool:
    return True  # No requirements, Open-Meteo is free


# --- Registry ---
from tools.registry import registry

registry.register(
    name="get_weather",
    toolset="weather",
    schema=WEATHER_SCHEMA,
    handler=lambda args, **kw: get_weather(
        location=args.get("location"),
        latitude=args.get("latitude"),
        longitude=args.get("longitude"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_weather_requirements,
    emoji="🌤️",
)
