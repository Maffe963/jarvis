"""Weather from Open-Meteo (free, no API key)."""
import json
import os
import urllib.parse
import urllib.request

CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
    81: "rain showers", 82: "heavy rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


def _get(url: str, params: dict) -> dict:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "Jarvis/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_weather(location: str = "", days: int = 1) -> str:
    location = location.strip() or os.environ.get("JARVIS_LOCATION", "").strip()
    if not location:
        return "No location given and JARVIS_LOCATION isn't set. Ask the user which city."
    # The geocoder wants just the place name: "Boston, MA" -> "Boston", then prefer matches on the rest.
    name, _, hint = location.partition(",")
    places = _get("https://geocoding-api.open-meteo.com/v1/search",
                  {"name": name.strip(), "count": 10, "language": "en"}).get("results") or []
    if not places:
        return f"Couldn't find a place called '{location}'."
    hint = hint.strip().lower()
    place = next((p for p in places if hint and hint in " ".join(
        str(p.get(k, "")) for k in ("admin1", "country", "country_code")).lower()), places[0])

    days = max(1, min(int(days or 1), 7))
    data = _get("https://api.open-meteo.com/v1/forecast", {
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": os.environ.get("JARVIS_TEMP_UNIT", "fahrenheit"),
        "wind_speed_unit": "mph", "timezone": "auto", "forecast_days": days,
    })
    unit = "F" if os.environ.get("JARVIS_TEMP_UNIT", "fahrenheit") == "fahrenheit" else "C"
    now, daily = data["current"], data["daily"]
    where = ", ".join(str(p) for p in (place["name"], place.get("admin1")) if p)
    lines = [f"{where} now: {CODES.get(now['weather_code'], 'unknown')}, {now['temperature_2m']:.0f}{unit} "
             f"(feels {now['apparent_temperature']:.0f}{unit}), wind {now['wind_speed_10m']:.0f} mph."]
    for i, day in enumerate(daily["time"]):
        rain = daily["precipitation_probability_max"][i]
        lines.append(f"{day}: {CODES.get(daily['weather_code'][i], 'unknown')}, high {daily['temperature_2m_max'][i]:.0f}"
                     f"{unit}, low {daily['temperature_2m_min'][i]:.0f}{unit}"
                     + (f", {rain}% chance of precipitation" if rain is not None else "") + ".")
    return "\n".join(lines)


TOOLS = [{
    "name": "get_weather",
    "description": "Current weather and forecast. Omit location to use the user's home city (JARVIS_LOCATION).",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City, optionally with state/country, e.g. 'Boston, MA'"},
            "days": {"type": "integer", "description": "Days of forecast including today (1-7, default 1)"}
        },
        "required": []
    }
}]

TOOL_MAP = {"get_weather": get_weather}
