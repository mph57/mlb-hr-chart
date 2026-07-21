"""Batted-ball, recent-form, and matchup data.

Sources (all free, no keys):
  - Pitcher batted-ball profile (fly-ball %, hard-hit %): Baseball Savant's
    custom leaderboard CSV, keyed by MLBAM player id.
  - Batter recent form (last N days): Baseball Reference range splits via
    pybaseball, keyed by MLBAM id.
  - Batter-vs-pitcher career history: MLB StatsAPI ``vsPlayer`` split.
  - Recent-lineup fallback: most recent Final boxscore for a team, used when
    today's lineup has not been posted yet.

FanGraphs' leaderboards are intentionally avoided — pybaseball's FanGraphs
scraper currently returns HTTP 403, so everything here rides on Savant, BRef,
and StatsAPI, which remain open.
"""
from __future__ import annotations

import datetime as dt
import io
import warnings
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import pandas as pd
import requests

warnings.filterwarnings("ignore")

STATSAPI = "https://statsapi.mlb.com/api/v1"
SAVANT = "https://baseballsavant.mlb.com/leaderboard/custom"
UA = {"User-Agent": "Mozilla/5.0 (mlb-hr-chart)"}
TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Pitcher batted-ball profile (fly-ball % and hard-hit %)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def pitcher_batted_ball(year: int, min_bbe: int = 40) -> dict[int, dict]:
    """MLBAM id -> {fb_pct, gb_pct, ld_pct, pop_pct, hard_hit_pct}.

    Pulled once per season from Savant's custom leaderboard and cached.
    """
    params = {
        "year": str(year),
        "type": "pitcher",
        "filter": "",
        "min": str(min_bbe),
        "selections": ("player_id,ab,flyballs_percent,groundballs_percent,"
                       "linedrives_percent,popups_percent,hard_hit_percent"),
        "sort": "flyballs_percent",
        "sortDir": "desc",
        "csv": "true",
    }
    resp = requests.get(SAVANT, params=params, headers=UA, timeout=TIMEOUT)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    # The CSV repeats player_id; keep the numeric columns we asked for.
    df = df.loc[:, ~df.columns.duplicated()]

    out: dict[int, dict] = {}
    for _, r in df.iterrows():
        try:
            pid = int(r["player_id"])
        except (ValueError, TypeError):
            continue
        out[pid] = {
            "fb_pct": _num(r.get("flyballs_percent")),
            "gb_pct": _num(r.get("groundballs_percent")),
            "ld_pct": _num(r.get("linedrives_percent")),
            "pop_pct": _num(r.get("popups_percent")),
            "hard_hit_pct": _num(r.get("hard_hit_percent")),
        }
    return out


# --------------------------------------------------------------------------- #
# Batter recent form (last N days)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def recent_form(end_date: str, days: int = 7) -> dict[int, dict]:
    """MLBAM id -> {pa, ab, hr, ba, slg, ops, games} over the trailing window."""
    from pybaseball import batting_stats_range

    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=days)
    try:
        df = batting_stats_range(start.isoformat(), end.isoformat())
    except Exception:
        return {}

    out: dict[int, dict] = {}
    for _, r in df.iterrows():
        mlbid = r.get("mlbID")
        if pd.isna(mlbid):
            continue
        try:
            pid = int(mlbid)
        except (ValueError, TypeError):
            continue
        out[pid] = {
            "pa": _int(r.get("PA")),
            "ab": _int(r.get("AB")),
            "hr": _int(r.get("HR")),
            "ba": _num(r.get("BA")),
            "slg": _num(r.get("SLG")),
            "ops": _num(r.get("OPS")),
            "games": _int(r.get("G")),
        }
    return out


@lru_cache(maxsize=4)
def season_form(end_date: str) -> dict[int, dict]:
    """MLBAM id -> {pa, hr} for the season to date.

    Gives a stable HR-per-PA base rate for the probability model (the 7-day
    window in ``recent_form`` is too noisy to anchor a rate on its own).
    """
    from pybaseball import batting_stats_range

    end = dt.date.fromisoformat(end_date)
    start = dt.date(end.year, 3, 15)   # comfortably before Opening Day
    if end <= start:
        return {}
    try:
        df = batting_stats_range(start.isoformat(), end.isoformat())
    except Exception:
        return {}

    out: dict[int, dict] = {}
    for _, r in df.iterrows():
        mlbid = r.get("mlbID")
        if pd.isna(mlbid):
            continue
        try:
            pid = int(mlbid)
        except (ValueError, TypeError):
            continue
        out[pid] = {"pa": _int(r.get("PA")), "hr": _int(r.get("HR"))}
    return out


# --------------------------------------------------------------------------- #
# Batter vs. pitcher career history
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4096)
def bvp(batter_id: int, pitcher_id: int) -> dict | None:
    """Career batter-vs-pitcher totals, or None if they've never faced off."""
    if not batter_id or not pitcher_id:
        return None
    try:
        resp = requests.get(
            f"{STATSAPI}/people/{batter_id}/stats",
            params={"stats": "vsPlayer", "opposingPlayerId": pitcher_id,
                    "group": "hitting", "sportId": 1},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    for block in data.get("stats", []):
        if block.get("type", {}).get("displayName") == "vsPlayerTotal":
            splits = block.get("splits", [])
            if not splits:
                return None
            st = splits[0].get("stat", {})
            ab = _int(st.get("atBats"))
            if ab is None:
                return None
            return {
                "ab": ab,
                "h": _int(st.get("hits")),
                "hr": _int(st.get("homeRuns")),
                "ops": _num(st.get("ops")),
            }
    return None


def bvp_batch(pairs: list[tuple[int, int]], workers: int = 8) -> dict[tuple[int, int], dict | None]:
    """Fetch many (batter_id, pitcher_id) matchups concurrently (cached)."""
    results: dict[tuple[int, int], dict | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(bvp, b, p): (b, p) for b, p in set(pairs)}
        for fut in futs:
            pair = futs[fut]
            try:
                results[pair] = fut.result()
            except Exception:
                results[pair] = None
    return results


# --------------------------------------------------------------------------- #
# Recent-lineup fallback (most recent Final boxscore for a team)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=64)
def recent_lineup(team_id: int, before_date: str) -> list[dict]:
    """Batting order from a team's most recent completed game before a date.

    Used when today's lineup hasn't been posted. Returns the same shape the
    schedule module produces for a posted lineup.
    """
    end = dt.date.fromisoformat(before_date)
    start = end - dt.timedelta(days=10)
    try:
        sched = requests.get(
            f"{STATSAPI}/schedule",
            params={"sportId": 1, "teamId": team_id,
                    "startDate": start.isoformat(), "endDate": end.isoformat()},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        return []

    final_pks: list[tuple[str, int]] = []
    for day in sched.get("dates", []):
        for g in day.get("games", []):
            state = (g.get("status") or {}).get("abstractGameState")
            if state == "Final":
                final_pks.append((g.get("gameDate", ""), g.get("gamePk")))
    if not final_pks:
        return []
    final_pks.sort()
    _, game_pk = final_pks[-1]

    try:
        box = requests.get(f"{STATSAPI}/game/{game_pk}/boxscore", timeout=TIMEOUT).json()
    except Exception:
        return []

    for side in ("home", "away"):
        team_block = box.get("teams", {}).get(side, {})
        if team_block.get("team", {}).get("id") != team_id:
            continue
        players = team_block.get("players", {})
        rows = []
        for pdata in players.values():
            order = pdata.get("battingOrder")
            if not order:
                continue
            person = pdata.get("person", {})
            pos = (pdata.get("position") or {}).get("abbreviation")
            rows.append({
                "id": person.get("id"),
                "name": person.get("fullName"),
                "bats": None,  # filled by handedness lookup upstream if needed
                "order": order,
                "position": pos,
            })
        # battingOrder like "100","200"... starters end in "00"; keep those.
        starters = [r for r in rows if str(r["order"]).endswith("00")]
        starters.sort(key=lambda r: int(r["order"]))
        return starters[:9]
    return []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _num(v) -> float | None:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None
