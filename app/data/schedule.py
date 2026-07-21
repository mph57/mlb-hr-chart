"""Today's slate from the free MLB StatsAPI (no key required).

Pulls the schedule for a given date and, for each game, resolves:
  - venue + home/away teams (mapped to our park abbreviations)
  - probable starting pitchers (id, name, throwing hand)
  - batting orders once lineups are posted (id, name, bat side, slot)

StatsAPI posts lineups a few hours before first pitch. Until then a game's
``lineups`` come back empty and the scoring layer falls back to each team's
recent regulars (handled upstream).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import requests

from .parks import get_park

BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 20

# StatsAPI team id -> our abbreviation. Abbreviations match parks.py keys.
TEAM_ABBR: dict[int, str] = {
    109: "ARI", 144: "ATL", 110: "BAL", 111: "BOS", 112: "CHC", 145: "CWS",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC",
    108: "LAA", 119: "LAD", 146: "MIA", 158: "MIL", 142: "MIN", 121: "NYM",
    147: "NYY", 133: "OAK", 143: "PHI", 134: "PIT", 135: "SD", 137: "SF",
    136: "SEA", 138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 120: "WSH",
}


def _get(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _people_handedness(person_ids: list[int]) -> dict[int, dict]:
    """Batch-fetch bat side / pitch hand for a set of player ids."""
    if not person_ids:
        return {}
    ids = ",".join(str(i) for i in sorted(set(person_ids)))
    data = _get(f"{BASE}/people", {"personIds": ids})
    out: dict[int, dict] = {}
    for p in data.get("people", []):
        out[p["id"]] = {
            "bats": (p.get("batSide") or {}).get("code"),   # L / R / S
            "throws": (p.get("pitchHand") or {}).get("code"),  # L / R
        }
    return out


def _lineup(players: list[dict], hand: dict[int, dict]) -> list[dict]:
    batters: list[dict] = []
    for pl in players:
        pid = pl.get("id")
        if pid is None:
            continue
        batters.append({
            "id": pid,
            "name": pl.get("fullName"),
            "bats": hand.get(pid, {}).get("bats"),
            "order": pl.get("battingOrder"),   # may be None until posted
            "position": (pl.get("primaryPosition") or {}).get("abbreviation"),
        })
    return batters


def get_slate(date: str | None = None) -> list[dict[str, Any]]:
    """Return a list of game dicts for ``date`` (YYYY-MM-DD, default today)."""
    if date is None:
        date = dt.date.today().isoformat()

    sched = _get(f"{BASE}/schedule", {
        "sportId": 1,
        "date": date,
        "hydrate": "probablePitcher,lineups,team,venue",
    })

    games: list[dict[str, Any]] = []
    person_ids: list[int] = []

    for day in sched.get("dates", []):
        for g in day.get("games", []):
            home = g["teams"]["home"]["team"]
            away = g["teams"]["away"]["team"]
            home_abbr = TEAM_ABBR.get(home["id"])
            away_abbr = TEAM_ABBR.get(away["id"])
            venue_name = (g.get("venue") or {}).get("name")
            park = get_park(home_abbr, venue_name)

            home_pp = g["teams"]["home"].get("probablePitcher") or {}
            away_pp = g["teams"]["away"].get("probablePitcher") or {}

            lineups = g.get("lineups") or {}
            home_line = lineups.get("homePlayers") or []
            away_line = lineups.get("awayPlayers") or []

            # Collect ids needing handedness lookup.
            for pp in (home_pp, away_pp):
                if pp.get("id"):
                    person_ids.append(pp["id"])
            for pl in home_line + away_line:
                if pl.get("id"):
                    person_ids.append(pl["id"])

            games.append({
                "game_pk": g.get("gamePk"),
                "status": (g.get("status") or {}).get("detailedState"),
                "game_time": g.get("gameDate"),  # ISO UTC
                "venue": venue_name,
                "park_abbr": home_abbr,
                "park": park,
                "home": {"id": home["id"], "abbr": home_abbr, "name": home["name"],
                         "probable": home_pp, "lineup_raw": home_line},
                "away": {"id": away["id"], "abbr": away_abbr, "name": away["name"],
                         "probable": away_pp, "lineup_raw": away_line},
            })

    # One batched handedness lookup for every player on the slate.
    hand = _people_handedness(person_ids)

    for game in games:
        for side in ("home", "away"):
            s = game[side]
            pp = s.pop("probable")
            s["pitcher"] = {
                "id": pp.get("id"),
                "name": pp.get("fullName"),
                "throws": hand.get(pp.get("id"), {}).get("throws"),
            } if pp.get("id") else None
            s["lineup"] = _lineup(s.pop("lineup_raw"), hand)

    return games
