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


def match_key(name: str, team: str | None = None) -> str:
    """Canonical join key: '<first-initial> <last>', optionally team-qualified.

    Bridges full names ("Byron Buxton") and abbreviated feeds ("B.Buxton") to
    the same key ("b buxton"). A team prefix ("min|b buxton") disambiguates the
    occasional shared initial+last on a slate.
    """
    if not name:
        return ""
    raw = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    raw = raw.lower().replace(".", " ").replace(",", " ").replace("'", "")
    parts = [p for p in raw.split() if p]
    while parts and parts[-1] in ("jr", "sr", "ii", "iii", "iv"):
        parts.pop()
    if not parts:
        return ""
    key = parts[0] if len(parts) == 1 else f"{parts[0][0]} {parts[-1]}"
    return f"{team.lower()}|{key}" if team else key


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
            out[match_key(name, r.get("team"))] = q
            out.setdefault(match_key(name), q)   # name-only fallback
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
                    nk = match_key(player)
                    # Keep the best (highest) Over price across books.
                    if nk not in out or q["american"] > out[nk]["american"]:
                        out[nk] = q
    return out


# --------------------------------------------------------------------------- #
# Action Network provider (free public JSON API)
# --------------------------------------------------------------------------- #
AN_API = "https://api.actionnetwork.com/web/v2"
AN_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
              "Referer": "https://www.actionnetwork.com/"}
# Action Network book ids -> display names (the set exposed without an account).
AN_BOOKS = {15: "Consensus", 30: "Open", 49: "Caesars", 68: "PointsBet",
            69: "FanDuel", 75: "BetMGM", 79: "Caesars", 76: "bet365",
            123: "DraftKings", 972: "ESPN BET"}
# Action Network uses ATH for the Athletics; our parks table uses OAK.
AN_ABBR_ALIAS = {"ATH": "OAK"}
# HR "to hit a home run" market (Over/Under 0.5).
AN_HR_MARKET = "core_bet_type_33_hr"


def _an_extract_book(lines: dict, book_id: int) -> tuple:
    """(over, under) American odds for one book, or (None, None). 0 => missing."""
    over = under = None
    for o in lines.get(str(book_id), []):
        if o.get("value") != 0.5:
            continue
        odds = o.get("odds")
        if not odds:
            continue
        if o.get("side") == "over":
            over = odds
        elif o.get("side") == "under":
            under = odds
    return over, under


def _load_actionnetwork(date: str) -> dict[str, dict]:
    """Scrape today's HR props from Action Network's public API.

    Prefers the Consensus line (both sides, so we can de-vig); falls back to any
    book quoting an Over. Names are matched by team + initial/last.
    """
    import requests as _rq

    day = date.replace("-", "")
    pref_book = int(os.environ.get("AN_BOOK_ID", "15"))  # 15 = Consensus
    try:
        sb = _rq.get(f"{AN_API}/scoreboard/mlb",
                     params={"period": "game", "date": day},
                     headers=AN_HEADERS, timeout=20).json()
    except Exception:
        return {}

    games = sb.get("games", [])
    an_team = {}
    for g in games:
        for t in g.get("teams", []):
            abbr = t.get("abbr")
            an_team[t.get("id")] = AN_ABBR_ALIAS.get(abbr, abbr)

    out: dict[str, dict] = {}

    def fetch_game(gid):
        try:
            return _rq.get(f"{AN_API}/games/{gid}/props",
                           headers=AN_HEADERS, timeout=25).json()
        except Exception:
            return None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        payloads = list(ex.map(fetch_game, [g.get("id") for g in games]))

    for data in payloads:
        if not data:
            continue
        for r in data.get("player_props", {}).get(AN_HR_MARKET, []):
            name = r.get("player_abbr") or r.get("player_last_name")
            lines = r.get("lines") or {}
            team = an_team.get(r.get("team_id"))

            over, under = _an_extract_book(lines, pref_book)
            book = pref_book
            if over is None:                       # fall back to any book
                for bid in lines:
                    o2, u2 = _an_extract_book(lines, int(bid))
                    if o2 is not None:
                        over, under, book = o2, u2, int(bid)
                        break
            if over is None:
                continue

            q = _quote(name, over, AN_BOOKS.get(book, f"book{book}"), under=under)
            if not q:
                continue
            out[match_key(name, team)] = q
            out.setdefault(match_key(name), q)      # name-only fallback
    return out


# --------------------------------------------------------------------------- #
# public entry
# --------------------------------------------------------------------------- #
def get_hr_odds(date: str) -> dict[str, dict]:
    """Return {match_key: quote} for the configured provider (or {})."""
    provider = os.environ.get("ODDS_PROVIDER", "actionnetwork").lower()
    try:
        if provider == "actionnetwork":
            return _load_actionnetwork(date)
        if provider == "theoddsapi":
            return _load_theoddsapi(date)
        return _load_file(date)
    except Exception:
        return {}
