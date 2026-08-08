"""Batted-ball, recent-form, and matchup data.

Sources (all free, no keys):
  - Pitcher batted-ball profile (fly-ball %, hard-hit %): Baseball Savant's
    custom leaderboard CSV, keyed by MLBAM player id.
  - Batter recent form (last N days): MLB StatsAPI ``byDateRange`` hitting
    leaderboard, keyed by MLBAM id.
  - Batter-vs-pitcher career history: MLB StatsAPI ``vsPlayer`` split.
  - Recent-lineup fallback: most recent Final boxscore for a team, used when
    today's lineup has not been posted yet.

Everything rides on Savant and StatsAPI, which stay reachable from hosted boxes.
Baseball-Reference/pybaseball scrapers are avoided on purpose: they get blocked
(HTTP 403) or rate-limited from server IPs, which silently left recent form
neutral on deploy.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore")

STATSAPI = "https://statsapi.mlb.com/api/v1"
SAVANT = "https://baseballsavant.mlb.com/leaderboard/custom"
UA = {"User-Agent": "Mozilla/5.0 (mlb-hr-chart)"}
TIMEOUT = 30
SPLIT_TIMEOUT = 8       # fail fast on a slow/throttled split call -> neutral

# On-disk cache so per-player season splits are fetched once per season, not on
# every cold board build (they change slowly). Survives server restarts.
CACHE_DIR = Path(__file__).resolve().parents[2] / "instance" / "cache"
_CACHE_LOCK = threading.Lock()


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
def _date_range_hitting(start: str, end: str) -> dict[int, dict]:
    """MLBAM id -> full hitting line over [start, end] via MLB StatsAPI.

    Uses the league-wide ``byDateRange`` leaderboard — one call for every hitter,
    keyed by MLBAM id. StatsAPI stays reachable from hosted boxes, unlike the
    Baseball-Reference range scraper (pybaseball), which gets blocked/rate-limited
    from server IPs and silently left recent form neutral on deploy.
    """
    resp = requests.get(
        f"{STATSAPI}/stats",
        params={"stats": "byDateRange", "group": "hitting",
                "startDate": start, "endDate": end,
                "sportId": 1, "playerPool": "All", "limit": 2000},
        headers=UA, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    out: dict[int, dict] = {}
    for block in data.get("stats", []):
        for sp in block.get("splits", []):
            pid = (sp.get("player") or {}).get("id")
            if pid is None:
                continue
            st = sp.get("stat", {})
            out[int(pid)] = {
                "pa": _int(st.get("plateAppearances")),
                "ab": _int(st.get("atBats")),
                "hr": _int(st.get("homeRuns")),
                "ba": _num(st.get("avg")),
                "slg": _num(st.get("slg")),
                "ops": _num(st.get("ops")),
                "games": _int(st.get("gamesPlayed")),
            }
    return out


@lru_cache(maxsize=8)
def recent_form(end_date: str, days: int = 7) -> dict[int, dict]:
    """MLBAM id -> {pa, ab, hr, ba, slg, ops, games} over the trailing window."""
    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=days)
    try:
        return _date_range_hitting(start.isoformat(), end.isoformat())
    except Exception:
        return {}


@lru_cache(maxsize=4)
def season_form(end_date: str) -> dict[int, dict]:
    """MLBAM id -> {pa, hr} for the season to date.

    Gives a stable HR-per-PA base rate for the probability model (the 7-day
    window in ``recent_form`` is too noisy to anchor a rate on its own).
    """
    end = dt.date.fromisoformat(end_date)
    start = dt.date(end.year, 3, 15)   # comfortably before Opening Day
    if end <= start:
        return {}
    try:
        full = _date_range_hitting(start.isoformat(), end.isoformat())
    except Exception:
        return {}
    return {pid: {"pa": v["pa"], "hr": v["hr"]} for pid, v in full.items()}


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


def bvp_batch(pairs: list[tuple[int, int]], workers: int = 16) -> dict[tuple[int, int], dict | None]:
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
# Platoon splits (batter performance vs LHP / RHP this season)
# --------------------------------------------------------------------------- #
def batter_splits(batter_id: int, season: int) -> dict:
    """{'R': {pa,hr,slg}, 'L': {pa,hr,slg}} — the batter's season vs each hand.

    Handles switch hitters automatically: their vs-R / vs-L lines already reflect
    hitting from the platoon-advantaged side. Returns {} on any failure/timeout.
    """
    if not batter_id:
        return {}
    try:
        resp = requests.get(
            f"{STATSAPI}/people/{batter_id}/stats",
            params={"stats": "statSplits", "season": season,
                    "sitCodes": "vr,vl", "group": "hitting"},
            timeout=SPLIT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}

    out: dict[str, dict] = {}
    code_to_hand = {"vr": "R", "vl": "L"}
    for block in data.get("stats", []):
        for sp in block.get("splits", []):
            hand = code_to_hand.get(sp.get("split", {}).get("code"))
            if not hand:
                continue
            st = sp.get("stat", {})
            out[hand] = {
                "pa": _int(st.get("plateAppearances")),
                "hr": _int(st.get("homeRuns")),
                "slg": _num(st.get("slg")),
            }
    return out


def _splits_cache_path(season: int) -> Path:
    return CACHE_DIR / f"splits_{season}.json"


def _load_splits_cache(season: int) -> dict[str, dict]:
    path = _splits_cache_path(season)
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_splits_cache(season: int, cache: dict[str, dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _splits_cache_path(season).write_text(json.dumps(cache))
    except Exception:
        pass


def batter_splits_batch(batter_ids: list[int], season: int,
                        workers: int = 8) -> dict[int, dict]:
    """Platoon splits for many batters, disk-cached per season.

    Only players missing from the on-disk cache are fetched (concurrently, with
    a fail-fast timeout), so after the first build of the day this is instant.
    """
    ids = [b for b in set(batter_ids) if b]
    with _CACHE_LOCK:
        cache = _load_splits_cache(season)

    missing = [b for b in ids if str(b) not in cache]
    if missing:
        fetched: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(batter_splits, b, season): b for b in missing}
            for fut in futs:
                b = futs[fut]
                try:
                    fetched[str(b)] = fut.result()
                except Exception:
                    fetched[str(b)] = {}
        with _CACHE_LOCK:
            cache = _load_splits_cache(season)   # re-read in case of concurrent write
            cache.update(fetched)
            _save_splits_cache(season, cache)

    return {b: cache.get(str(b), {}) for b in ids}


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
