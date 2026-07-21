"""Assemble and rank the daily home-run board.

Each batter on the slate gets a 0-100 **HR Score** blended from five signals,
each first mapped onto its own 0-100 sub-score (50 = league-neutral):

    weather      0.15   game-time carry (temp + wind out/in), per park
    park         0.15   ballpark HR factor
    pitcher      0.25   opposing starter's fly-ball % and hard-hit % allowed
    form         0.30   batter's last-7-day SLG and HR rate
    bvp          0.15   batter's career line vs today's starter (sample-damped)

Weights live in ``WEIGHTS`` — tune freely. Sub-scores are clamped to [0, 100]
and small-sample inputs (recent PA, career AB vs pitcher) are regressed toward
neutral so a 2-for-3 fluke can't dominate the board.
"""
from __future__ import annotations

import datetime as dt

from .data.parks import get_park
from .data.odds import get_hr_odds, match_key
from .data.schedule import get_slate
from .data import statcast
from .data.weather import game_weather

WEIGHTS = {
    "weather": 0.15,
    "park": 0.15,
    "pitcher": 0.25,
    "form": 0.30,
    "bvp": 0.15,
}

# League-ish reference points used to center the sub-scores.
LG_FB_PCT = 24.0        # pitcher fly-ball % allowed
LG_HARDHIT_PCT = 39.0   # pitcher hard-hit % allowed
LG_SLG = 0.400
LG_HR_PER_PA = 0.033
LG_OPS = 0.750


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def _park_score(hr_factor: float) -> float:
    return _clamp(50.0 + (hr_factor - 100.0) * 2.5)


def _pitcher_score(prof: dict | None) -> tuple[float, dict]:
    if not prof or prof.get("fb_pct") is None:
        return 50.0, {"fb_pct": None, "hard_hit_pct": None}
    fb = prof["fb_pct"]
    hh = prof.get("hard_hit_pct")
    fb_term = 50.0 + (fb - LG_FB_PCT) * 3.0
    hh_term = 50.0 + ((hh - LG_HARDHIT_PCT) * 2.5) if hh is not None else 50.0
    score = _clamp(0.5 * fb_term + 0.5 * hh_term)
    return score, {"fb_pct": fb, "hard_hit_pct": hh}


def _form_score(form: dict | None) -> tuple[float, dict]:
    if not form or not form.get("pa"):
        return 50.0, {"pa": 0, "hr": 0, "slg": None, "ops": None}
    pa = form["pa"]
    slg = form.get("slg") or 0.0
    hr = form.get("hr") or 0
    hr_per_pa = hr / pa if pa else 0.0
    slg_term = 50.0 + (slg - LG_SLG) * 125.0
    hr_term = 50.0 + (hr_per_pa - LG_HR_PER_PA) * 600.0
    raw = 0.5 * slg_term + 0.5 * hr_term
    trust = min(pa / 15.0, 1.0)          # damp thin samples toward neutral
    score = _clamp(50.0 + trust * (raw - 50.0))
    return score, {"pa": pa, "hr": hr, "slg": slg, "ops": form.get("ops")}


def _bvp_score(bvp: dict | None) -> tuple[float, dict]:
    if not bvp or not bvp.get("ab"):
        return 50.0, {"ab": 0, "hr": 0, "ops": None, "history": False}
    ab = bvp["ab"]
    hr = bvp.get("hr") or 0
    ops = bvp.get("ops")
    ops_term = ((ops - LG_OPS) * 40.0) if ops is not None else 0.0
    raw = ops_term + hr * 20.0
    trust = min(ab / 20.0, 1.0)
    score = _clamp(50.0 + trust * raw)
    return score, {"ab": ab, "hr": hr, "ops": ops, "history": True}


def _score_batter(batter, pitcher, park, weather, pit_prof,
                  form_map, bvp_map, odds_map, team=None):
    weather_s = weather["favor"]
    park_s = _park_score(park["hr_factor"])
    pitcher_s, pit_detail = _pitcher_score(pit_prof)
    form_s, form_detail = _form_score(form_map.get(batter["id"]))
    bvp_raw = bvp_map.get((batter["id"], pitcher["id"] if pitcher else None))
    bvp_s, bvp_detail = _bvp_score(bvp_raw)

    total = (
        WEIGHTS["weather"] * weather_s
        + WEIGHTS["park"] * park_s
        + WEIGHTS["pitcher"] * pitcher_s
        + WEIGHTS["form"] * form_s
        + WEIGHTS["bvp"] * bvp_s
    )

    # Today's HR prop line for this batter, if we have one (name shown as-is).
    quote = (odds_map.get(match_key(batter["name"], team))
             or odds_map.get(match_key(batter["name"])))

    return {
        "batter": batter["name"],
        "batter_id": batter["id"],
        "bats": batter.get("bats"),
        "order": batter.get("order"),
        "hr_score": round(total, 1),
        "sub": {
            "weather": round(weather_s, 1),
            "park": round(park_s, 1),
            "pitcher": round(pitcher_s, 1),
            "form": round(form_s, 1),
            "bvp": round(bvp_s, 1),
        },
        "odds": quote,          # {american, book, implied, ...} or None
        "pitcher_detail": pit_detail,
        "form_detail": form_detail,
        "bvp_detail": bvp_detail,
    }


def get_board(date: str | None = None) -> dict:
    """Build the ranked HR board for ``date`` (YYYY-MM-DD, default today)."""
    if date is None:
        date = dt.date.today().isoformat()
    year = int(date[:4])

    slate = get_slate(date)
    pit_bb = _safe(lambda: statcast.pitcher_batted_ball(year), {})
    form_map = _safe(lambda: statcast.recent_form(date, days=7), {})
    odds_map = _safe(lambda: get_hr_odds(date), {})

    # Resolve lineups (posted or fallback) and gather every BvP pair up front.
    bvp_pairs: list[tuple[int, int]] = []
    for g in slate:
        for side, opp in (("home", "away"), ("away", "home")):
            pitcher = g[opp]["pitcher"]
            lineup = g[side]["lineup"]
            if not lineup and g[side].get("id"):
                lineup = _safe(lambda tid=g[side]["id"]:
                               statcast.recent_lineup(tid, date), [])
                g[side]["lineup"] = lineup
                g[side]["lineup_projected"] = True
            else:
                g[side]["lineup_projected"] = False
            if pitcher and pitcher.get("id"):
                for b in lineup:
                    if b.get("id"):
                        bvp_pairs.append((b["id"], pitcher["id"]))

    bvp_map = _safe(lambda: statcast.bvp_batch(bvp_pairs), {})

    games_out = []
    all_rows = []
    for g in slate:
        park = g["park"]
        weather = _safe(lambda: game_weather(park, g["game_time"]),
                        {"favor": 50.0, "note": "n/a", "roof": park.get("roof")})
        game_entry = {
            "game_pk": g["game_pk"],
            "venue": g["venue"],
            "park_abbr": g["park_abbr"],
            "hr_factor": park["hr_factor"],
            "game_time": g["game_time"],
            "status": g["status"],
            "weather": weather,
            "matchups": [],
        }
        for side, opp in (("home", "away"), ("away", "home")):
            pitcher = g[opp]["pitcher"]
            pit_prof = pit_bb.get(pitcher["id"]) if pitcher and pitcher.get("id") else None
            rows = []
            for b in g[side]["lineup"]:
                if not b.get("id"):
                    continue
                row = _score_batter(b, pitcher, park, weather, pit_prof,
                                    form_map, bvp_map, odds_map,
                                    team=g[side]["abbr"])
                row.update({
                    "team": g[side]["abbr"],
                    "opp_pitcher": pitcher["name"] if pitcher else None,
                    "opp_throws": pitcher["throws"] if pitcher else None,
                    "venue": g["venue"],
                    "park_abbr": g["park_abbr"],
                    "game_time": g["game_time"],
                    "projected_lineup": g[side]["lineup_projected"],
                })
                rows.append(row)
                all_rows.append(row)
            game_entry["matchups"].append({
                "team": g[side]["abbr"],
                "opp_pitcher": pitcher["name"] if pitcher else None,
                "opp_throws": pitcher["throws"] if pitcher else None,
                "pitcher_profile": pit_prof,
                "projected_lineup": g[side]["lineup_projected"],
                "batters": rows,
            })
        games_out.append(game_entry)

    all_rows.sort(key=lambda r: r["hr_score"], reverse=True)

    import os
    matched = sum(1 for r in all_rows if r.get("odds"))
    odds_meta = {
        "provider": os.environ.get("ODDS_PROVIDER", "actionnetwork"),
        "quotes_loaded": len(odds_map),
        "batters_matched": matched,
    }

    return {
        "date": date,
        "generated_at": None,   # stamped by the caller (no clock in scoring)
        "weights": WEIGHTS,
        "num_games": len(slate),
        "leaderboard": all_rows,
        "games": games_out,
        "odds": odds_meta,
    }


def _safe(fn, default):
    """Run a data-fetch, swallowing failures so one dead source can't 500 the page."""
    try:
        return fn()
    except Exception:
        return default
