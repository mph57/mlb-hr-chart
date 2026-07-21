"""HR-prop odds — a pluggable provider layer.

The board never talks to a book directly; it asks ``get_hr_odds(date)`` for a
mapping of **normalized batter name -> quote**, and each quote carries the raw
American price plus a decimal price and vig-included implied probability.

Two providers ship today, chosen by the ``ODDS_PROVIDER`` env var:

  file        (default) — read a hand-maintained file at
                          ``instance/odds/<date>.(json|csv)``. Paste lines off
                          any book; no key, no cost. See ``sample_odds.csv``.
  theoddsapi             — live pull from The Odds API's ``batter_home_runs``
                          market. Fully implemented; needs a paid Business key
                          in ``ODDS_API_KEY``. Drop the key in and flip the env
                          var — no other code changes.

Matching to batters is by name (manual paste has no player ids), so names are
normalized (accent-stripped, suffix-stripped, lowercased) on both sides.
"""
from __future__ import annotations

import csv
import json
import os
import unicodedata
from pathlib import Path

import requests

INSTANCE = Path(__file__).resolve().parents[2] / "instance"
ODDS_DIR = INSTANCE / "odds"


# --------------------------------------------------------------------------- #
# odds math
# --------------------------------------------------------------------------- #
def american_to_decimal(american: float) -> float:
    return (american / 100.0) + 1.0 if american > 0 else (100.0 / -american) + 1.0


def american_to_implied(american: float) -> float:
    """Vig-included implied probability of the Over hitting."""
    return 1.0 / american_to_decimal(american)


def prob_to_american(p: float) -> int | None:
    """Fair American odds for a probability (rounded to whole)."""
    if not p or p <= 0 or p >= 1:
        return None
    return round(-(p / (1 - p)) * 100) if p >= 0.5 else round(((1 - p) / p) * 100)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower().replace(".", "").replace(",", "").replace("'", "")
    for suffix in (" jr", " sr", " ii", " iii", " iv"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return " ".join(n.split())


def _quote(name: str, american, book: str | None, under=None) -> dict | None:
    try:
        am = float(american)
    except (TypeError, ValueError):
        return None
    if am == 0:
        return None
    q = {
        "name": name,
        "book": book,
        "american": int(am) if float(am).is_integer() else am,
        "decimal": round(american_to_decimal(am), 3),
        "implied": round(american_to_implied(am), 4),
        "implied_novig": None,
    }
    # If an Under price is supplied, de-vig to the true Over probability.
    try:
        if under not in (None, ""):
            iu = american_to_implied(float(under))
            q["implied_novig"] = round(q["implied"] / (q["implied"] + iu), 4)
    except (TypeError, ValueError):
        pass
    return q


# --------------------------------------------------------------------------- #
# file / paste provider
# --------------------------------------------------------------------------- #
def _load_file(date: str) -> dict[str, dict]:
    """Read instance/odds/<date>.json or .csv into {normalized_name: quote}."""
    out: dict[str, dict] = {}
    j = ODDS_DIR / f"{date}.json"
    c = ODDS_DIR / f"{date}.csv"

    rows: list[dict] = []
    if j.exists():
        try:
            data = json.loads(j.read_text())
            rows = data.get("odds", data) if isinstance(data, dict) else data
        except Exception:
            rows = []
    elif c.exists():
        try:
            with c.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
        except Exception:
            rows = []

    for r in rows:
        name = r.get("player") or r.get("name")
        american = r.get("over") if r.get("over") not in (None, "") else r.get("american")
        q = _quote(name, american, r.get("book"), under=r.get("under"))
        if q:
            out[normalize_name(name)] = q
    return out


# --------------------------------------------------------------------------- #
# The Odds API provider (paid Business plan for player props)
# --------------------------------------------------------------------------- #
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb"


def _load_theoddsapi(date: str) -> dict[str, dict]:
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return {}
    regions = os.environ.get("ODDS_API_REGIONS", "us")
    books_pref = os.environ.get("ODDS_API_BOOKMAKER")  # optional single book
    try:
        events = requests.get(f"{ODDS_API_BASE}/events",
                              params={"apiKey": key}, timeout=25).json()
    except Exception:
        return {}

    out: dict[str, dict] = {}
    for ev in events if isinstance(events, list) else []:
        if not (ev.get("commence_time", "").startswith(date)):
            continue
        try:
            odds = requests.get(
                f"{ODDS_API_BASE}/events/{ev['id']}/odds",
                params={"apiKey": key, "regions": regions,
                        "markets": "batter_home_runs", "oddsFormat": "american"},
                timeout=25,
            ).json()
        except Exception:
            continue
        for bm in odds.get("bookmakers", []):
            if books_pref and bm.get("key") != books_pref:
                continue
            for mk in bm.get("markets", []):
                if mk.get("key") != "batter_home_runs":
                    continue
                for oc in mk.get("outcomes", []):
                    # "Over" 0.5 outcome carries the player name in `description`.
                    if oc.get("name", "").lower() != "over":
                        continue
                    player = oc.get("description")
                    q = _quote(player, oc.get("price"), bm.get("title"))
                    if not q:
                        continue
                    nk = normalize_name(player)
                    # Keep the best (highest) Over price across books.
                    if nk not in out or q["american"] > out[nk]["american"]:
                        out[nk] = q
    return out


# --------------------------------------------------------------------------- #
# public entry
# --------------------------------------------------------------------------- #
def get_hr_odds(date: str) -> dict[str, dict]:
    """Return {normalized_name: quote} for the configured provider (or {})."""
    provider = os.environ.get("ODDS_PROVIDER", "file").lower()
    try:
        if provider == "theoddsapi":
            return _load_theoddsapi(date)
        return _load_file(date)
    except Exception:
        return {}
