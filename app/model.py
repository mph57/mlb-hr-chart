"""Home-run probability + betting edge.

Separate from ``scoring.py`` on purpose: the 0-100 **HR Score** is a soft
ranking blend (it folds in noisy signals like BvP and a hot week), while this
module produces an actual **P(batter hits >=1 HR today)** that can be compared
to a sportsbook's implied probability to find value.

Probability build:

    base_rate   season HR/PA, regressed toward league (~0.033) by a prior so
                low-PA hitters don't spike. A small nudge from the last-7-day
                rate lets genuinely hot/cold bats move.
    per-PA p    base_rate * park_mult * weather_mult * pitcher_mult (each a
                multiplicative context factor centered on 1.0, clamped).
    game p      1 - (1 - per_PA_p) ^ expected_PA, where expected PA is inferred
                from lineup slot.

Edge = model P - market implied P (percentage points). EV is per $1 on the
Over at the quoted decimal price. Fair odds are the model's own break-even line.
"""
from __future__ import annotations

import math
import os

from .data.odds import prob_to_american

LG_HR_PER_PA = 0.033
PRIOR_PA = 120          # strength of regression toward league base rate
RECENT_BLEND = 0.15     # weight on recent rate vs. season base (when trusted)
RECENT_MIN_PA = 25      # only let recent form move the rate with a real sample
RECENT_CAP = 0.11       # cap the recent HR/PA it can contribute (anti-fluke)

# A HR "Over 0.5" price is heavily juiced on the Under side, so the raw Over
# implied prob overstates the true prob by the vig. Unless we can de-vig with a
# posted Under price, only flag value when the edge clears this cushion (points).
VALUE_THRESHOLD_PP = float(os.environ.get("VALUE_THRESHOLD_PP", "3.0"))

# The probability model regresses weak hitters toward the league mean, so it
# over-projects deep longshots that a sharp market prices far lower — those
# "edges" are model error, not value. Don't flag VALUE on prices longer than
# this (the edge is still shown; only the badge is suppressed). With market
# anchoring below this rarely binds now; kept as a backstop.
VALUE_MAX_ODDS = int(os.environ.get("VALUE_MAX_ODDS", "600"))

# Market anchoring. A sharp HR-prop line already prices a batter's true talent
# plus the matchup, so we treat the de-vigged market prob as a strong prior and
# only keep a fraction of our disagreement with it. That fraction (k) shrinks
# for longshots, where the model is least trustworthy, and is full for plausibly
# priced bats, where our context signals (wind, form) can legitimately differ.
ANCHOR_K = float(os.environ.get("ANCHOR_K", "0.5"))   # max share of disagreement kept
ANCHOR_P_REF = float(os.environ.get("ANCHOR_P_REF", "0.15"))  # "plausible" market prob


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def market_anchored(model_prob: float, market_prob: float) -> float:
    """Blend model toward the market in log-odds, shrinking more for longshots.

    k = ANCHOR_K * clamp(market_prob / ANCHOR_P_REF, 0.25, 1.0): a +1100 bat
    (market ~8%) keeps ~half the weight a +200 bat does, so its inflated model
    edge collapses toward the line while a fairly priced bat's genuine edge
    largely survives.
    """
    if market_prob <= 0:
        return model_prob
    k = ANCHOR_K * _clampf(market_prob / ANCHOR_P_REF, 0.25, 1.0)
    blended = _logit(market_prob) + k * (_logit(model_prob) - _logit(market_prob))
    return _sigmoid(blended)

# Expected plate appearances by batting-order slot (1-9). Top of order sees
# more PAs; unknown slots fall back to a league-ish 4.0.
EXP_PA_BY_SLOT = {1: 4.5, 2: 4.4, 3: 4.3, 4: 4.2, 5: 4.1,
                  6: 3.9, 7: 3.8, 8: 3.7, 9: 3.6}


def _clampf(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _slot(order) -> int | None:
    """Map a StatsAPI battingOrder ('100','200'...) or 1-9 int to a slot 1-9."""
    if order is None:
        return None
    try:
        n = int(order)
    except (ValueError, TypeError):
        return None
    if n >= 100:               # boxscore style: 100,200,...,900 (+ subs 101..)
        return max(1, min(9, n // 100))
    return max(1, min(9, n)) if 1 <= n <= 9 else None


def base_rate(season: dict | None, recent: dict | None) -> float:
    """Regressed season HR-per-PA, nudged by recent form only on a real sample.

    Recency lives mostly in the soft HR Score; here we want a stable true-talent
    rate, so a hot 3-for-12 week can't spike the price. It moves the rate only
    with >= ``RECENT_MIN_PA`` recent PAs and a capped recent rate.
    """
    s_pa = (season or {}).get("pa") or 0
    s_hr = (season or {}).get("hr") or 0
    reg = (s_hr + LG_HR_PER_PA * PRIOR_PA) / (s_pa + PRIOR_PA)

    r_pa = (recent or {}).get("pa") or 0
    if r_pa >= RECENT_MIN_PA:
        r_rate = min(((recent or {}).get("hr") or 0) / r_pa, RECENT_CAP)
        return (1 - RECENT_BLEND) * reg + RECENT_BLEND * r_rate
    return reg


def context_mult(hr_factor: float, weather_favor: float,
                 pitcher_subscore: float) -> dict:
    """Multiplicative park / weather / pitcher context factors (centered 1.0).

    Deliberately gentle: park factor already absorbs some of a venue's climate,
    so weather and pitcher adjustments are damped to avoid triple-counting the
    same tailwind into an unrealistic probability.
    """
    park = _clampf(1.0 + (hr_factor / 100.0 - 1.0) * 0.85, 0.88, 1.15)
    weather = _clampf(1.0 + (weather_favor - 50.0) / 180.0, 0.85, 1.18)
    pitcher = _clampf(1.0 + (pitcher_subscore - 50.0) / 180.0, 0.85, 1.18)
    combined = _clampf(park * weather * pitcher, 0.65, 1.45)
    return {"park": round(park, 3), "weather": round(weather, 3),
            "pitcher": round(pitcher, 3), "combined": round(combined, 3)}


def hr_probability(season, recent, order, hr_factor, weather_favor,
                   pitcher_subscore) -> dict:
    """Return model P(>=1 HR) plus its ingredients for display."""
    br = base_rate(season, recent)
    mult = context_mult(hr_factor, weather_favor, pitcher_subscore)
    p_pa = _clampf(br * mult["combined"], 0.0, 0.11)
    exp_pa = EXP_PA_BY_SLOT.get(_slot(order), 4.0)
    p_game = 1.0 - (1.0 - p_pa) ** exp_pa
    p_game = _clampf(p_game, 0.01, 0.42)
    return {
        "prob": round(p_game, 4),
        "fair_american": prob_to_american(p_game),
        "base_hr_per_pa": round(br, 4),
        "exp_pa": exp_pa,
        "mult": mult,
    }


def edge(model_prob: float, quote: dict | None) -> dict | None:
    """Compare model prob to a market quote. None if no quote.

    Edge is measured against the de-vigged (true) market probability when the
    Under price is available; otherwise against the raw Over implied, and the
    ``value`` flag then requires the edge to clear ``VALUE_THRESHOLD_PP`` so the
    book's hold isn't mistaken for an edge.
    """
    if not quote:
        return None
    dec = quote["decimal"]
    raw_implied = quote["implied"]
    novig = quote.get("implied_novig")
    market = novig if novig is not None else raw_implied

    # Our belief = model anchored to the (de-vigged) market. Edge and EV come
    # off this projection, not the raw model, so longshot noise can't inflate it.
    proj = market_anchored(model_prob, market)
    ev = proj * (dec - 1.0) - (1.0 - proj)      # per $1 on the Over
    edge_pp = (proj - market) * 100

    # Require a consistent edge cushion to flag VALUE — even de-vigged, a sub-3pp
    # anchored edge is within model noise, not a confident bet.
    value = edge_pp >= VALUE_THRESHOLD_PP
    # Backstop: never badge a deep longshot even if anchoring left a sliver.
    if quote["american"] > VALUE_MAX_ODDS:
        value = False

    return {
        "book": quote.get("book"),
        "american": quote["american"],
        "implied": round(raw_implied, 4),
        "implied_novig": round(novig, 4) if novig is not None else None,
        "market_prob": round(market, 4),
        "proj_prob": round(proj, 4),           # market-anchored belief
        "proj_pct": round(proj * 100, 1),
        "proj_fair": prob_to_american(proj),   # fair line for the anchored belief
        "edge_pp": round(edge_pp, 1),          # points vs the fair market prob
        "ev_pct": round(ev * 100, 1),          # % of stake (uses Over price)
        "value": value,
    }
