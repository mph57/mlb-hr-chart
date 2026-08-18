"""Game-time weather and a hitting-favorability score, from Open-Meteo (free).

For each park we pull the hourly forecast, pick the hour nearest first pitch,
and translate temperature + wind into a 0-100 favorability score (50 = neutral)
for how well the ball carries. Wind is projected onto the park's home-plate ->
center-field axis so we know whether it's blowing *out* (helps home runs) or
*in* (kills them). Closed roofs / domes neutralize weather entirely.

Physics rules of thumb baked into the score:
  - Warm air is thinner: roughly +2-3 ft of carry per 10 F above ~70 F.
  - Wind blowing out adds carry; ~10 mph straight out is a meaningful boost.
"""
from __future__ import annotations

import datetime as dt
import math

import requests

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "Mozilla/5.0 (mlb-hr-chart)"}
TIMEOUT = 20


def _bearing_desc(delta_deg: float) -> str:
    """Describe wind relative to the out-to-CF axis given the angular offset."""
    if delta_deg <= 45:
        return "out to CF"
    if delta_deg >= 135:
        return "in from CF"
    return "cross"


def game_weather(park: dict, game_time_iso: str | None) -> dict:
    """Return weather + favorability for a park at (approx.) game time.

    ``park`` is a record from parks.py. ``game_time_iso`` is the game's UTC
    ISO timestamp from the schedule. Returns a dict the scoring layer consumes;
    ``favor`` is always present (defaults to neutral 50 on any failure or roof).
    """
    roof = park.get("roof", "open")
    lat, lon = park.get("lat"), park.get("lon")

    # Domes are always neutral; retractables are treated as open here but the
    # note flags the uncertainty (we can't know the roof state pre-game).
    if roof == "dome" or lat is None or lon is None:
        return {"favor": 50.0, "roof": roof, "note": "indoor / neutral",
                "temp": None, "wind_speed": None, "wind_dir": None,
                "wind_desc": None, "out_component": None, "wind_angle": None,
                "humidity": None}

    try:
        game_dt = dt.datetime.fromisoformat(game_time_iso.replace("Z", "+00:00")) \
            if game_time_iso else None
    except Exception:
        game_dt = None
    target = game_dt or dt.datetime.now(dt.timezone.utc)
    date_str = target.date().isoformat()

    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
        "start_date": date_str, "end_date": date_str,
    }
    hourly = None
    reason = "unknown"
    for attempt in range(2):   # one quick retry — cold hosts hiccup on first hit
        try:
            resp = requests.get(OPEN_METEO, params=params, headers=UA, timeout=TIMEOUT)
            resp.raise_for_status()
            hourly = resp.json()["hourly"]
            break
        except Exception as e:   # surface the cause so deploy failures are diagnosable
            reason = f"{type(e).__name__}: {e}"[:120]
    if hourly is None:
        return {"favor": 50.0, "roof": roof, "note": f"weather unavailable ({reason})",
                "temp": None, "wind_speed": None, "wind_dir": None,
                "wind_desc": None, "out_component": None, "wind_angle": None,
                "humidity": None}

    # Pick the forecast hour closest to game time.
    times = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
             for t in hourly["time"]]
    idx = min(range(len(times)), key=lambda i: abs((times[i] - target).total_seconds()))

    temp = hourly["temperature_2m"][idx]
    humidity = hourly["relative_humidity_2m"][idx]
    wind_speed = hourly["wind_speed_10m"][idx]
    wind_dir = hourly["wind_direction_10m"][idx]  # direction wind comes FROM

    # Project wind onto the out-to-CF axis. Wind blows *toward* (from+180).
    cf = park.get("cf_azimuth", 0)
    blow_to = (wind_dir + 180) % 360
    delta = abs((blow_to - cf + 180) % 360 - 180)   # 0=straight out, 180=straight in
    out_component = wind_speed * math.cos(math.radians(delta))  # + out / - in
    wind_desc = _bearing_desc(delta)
    # Clockwise angle of the wind (where it blows *toward*) relative to the
    # out-to-CF axis, for drawing a dial: 0 = out to CF, 180 = in from CF.
    wind_angle = round((blow_to - cf) % 360)

    # Favorability: 50 neutral, temp and wind push it up/down.
    temp_term = (temp - 70.0) * 0.6            # +12 at 90F, -12 at 50F
    wind_term = out_component * 1.2            # +12 at 10 mph straight out
    favor = 50.0 + temp_term + wind_term
    favor = max(0.0, min(100.0, favor))

    if roof == "retractable":
        note = f"{wind_desc} {abs(out_component):.0f} mph (retractable roof)"
    else:
        note = f"{temp:.0f}F, wind {wind_desc} {abs(out_component):.0f} mph"

    return {
        "favor": round(favor, 1),
        "roof": roof,
        "note": note,
        "temp": round(temp, 1),
        "wind_speed": round(wind_speed, 1),
        "wind_dir": round(wind_dir),
        "wind_desc": wind_desc,
        "out_component": round(out_component, 1),
        "wind_angle": wind_angle,
        "humidity": round(humidity),
    }
