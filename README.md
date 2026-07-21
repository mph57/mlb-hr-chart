# ⚾ MLB Daily Home Run Board

A Flask web app that ranks every hitter on today's MLB slate by a blended
**HR Score**, built from the factors that actually drive home runs:

| Signal | Weight | Source |
|---|---|---|
| **Weather** — game-time temp + wind blowing out/in (per park orientation) | 15% | Open-Meteo |
| **Ballpark** — multi-year HR park factor | 15% | static table (`app/data/parks.py`) |
| **Pitcher** — opposing starter's fly-ball % and hard-hit % allowed | 25% | Baseball Savant (Statcast) |
| **Batter form** — last-7-day SLG + HR rate | 30% | Baseball-Reference |
| **BvP** — batter's career line vs today's starter (sample-damped) | 15% | MLB StatsAPI |

Each signal is mapped to a 0–100 sub-score (50 = league-neutral), then blended.
Small samples (recent PA, career AB vs pitcher) regress toward neutral so a
2-for-3 fluke can't dominate. Weights live in `WEIGHTS` in `app/scoring.py` —
tune them however you like.

> Model output only — **not betting advice.**

## HR prop odds & betting edge

On top of the ranking, the board computes an actual **P(batter hits ≥1 HR
today)** and compares it to a sportsbook line to find mispriced bats:

- **Raw model** — season HR/PA (regressed toward league, so low-PA bats don't
  spike) × gentle park/weather/pitcher context factors, compounded over the
  batter's expected plate appearances. Recency lives in the HR Score, *not*
  here, so a hot 3-for-12 week can't distort the price. This is the number shown
  for bats with no line, and as `raw` in the detail line.
- **Proj** — the raw model **anchored to the market**. A sharp HR line already
  prices talent + matchup, so we treat the de-vigged market prob as a strong
  prior and keep only a fraction of our disagreement with it (blend in log-odds).
  The kept fraction shrinks for longshots — where the model is least reliable —
  so a +1100 slap hitter's inflated edge collapses toward the line while a fairly
  priced bat's genuine edge mostly survives. Tunable via `ANCHOR_K` (max share
  of disagreement kept, default 0.5) and `ANCHOR_P_REF` (default 0.15).
- **Edge** — Proj minus the *de-vigged* market probability. `VALUE` fires only
  when the edge clears `VALUE_THRESHOLD_PP` (default 3pp) and the price is inside
  `VALUE_MAX_ODDS` (default +600). **Fair** is Proj's break-even line, **EV** the
  expected return per $1 on the Over.

> Because the market is efficient, expect **few or zero** VALUE badges most days
> — that's the tool being honest, not broken. The edge *sort* still ranks every
> bat so you can eyeball the best of a thin slate.

Sort the board by **Edge vs line** (toolbar) to surface value plays.

### Feeding in odds

Three providers, set by `ODDS_PROVIDER`:

**`actionnetwork` (default, free, no key)** — pulls today's HR props straight
from Action Network's public JSON API (`web/v2/games/{id}/props`,
`core_bet_type_33_hr`) across all games and prices against the **Consensus**
line (id 15), which carries both Over and Under so edges are de-vigged. Set
`AN_BOOK_ID` to price against a specific book instead (49 Caesars, 69 FanDuel).
Matches players by team + initial/last. Runs fine from this Mac; a hosted box
*may* get rate-limited (it's their internal endpoint), so keep `file` as backup.

The other two providers:

**`file` (default, free)** — drop a CSV or JSON at `instance/odds/<date>.csv`:

```csv
player,book,over,under
Kyle Schwarber,DK,+300,-400
Hunter Goodman,DK,+330,
```

`under` is optional — include it to de-vig to the true probability; leave it
blank to fall back to the threshold rule. Names are matched fuzzily (accents and
Jr./Sr. suffixes ignored). See `instance/odds/sample_odds.csv`.

**`theoddsapi`** — set `ODDS_PROVIDER=theoddsapi` and `ODDS_API_KEY=...` (paid
Business plan for the `batter_home_runs` market). Fully implemented — no code
changes, it just starts pulling live lines and de-vigging across books.

> Model output only — **not betting advice.**

## Run it locally

```bash
cd mlb-hr-chart
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python wsgi.py          # http://127.0.0.1:5001/today
```

- `/today` — the ranked board (add `?date=YYYY-MM-DD` for another day, `?limit=N`)
- `/api/board.json` — same data as JSON (`?date=`, `?limit=`)
- `POST /api/refresh?date=` — drop the cache and rebuild

Lineups post ~3 hours before first pitch. Before then the board fills each team
with its **projected** lineup (most recent Final boxscore), flagged `proj`.

## How the data flows

```
app/
  data/
    parks.py      30 ballparks: HR factor, lat/lon, roof, field orientation
    schedule.py   StatsAPI — games, probable pitchers, posted lineups
    statcast.py   Savant FB%/HardHit%, BRef 7-day form, StatsAPI BvP, lineup fallback
    weather.py    Open-Meteo — game-time carry score (temp + wind vs CF axis)
    odds.py       pluggable HR-prop providers (Action Network / file / Odds API) + de-vig
  model.py        P(>=1 HR) probability, fair odds, edge vs the market line
  scoring.py      blends the five signals + attaches model prob / edge per bat
  cache.py        in-process TTL cache (board build hits many live endpoints)
  main.py / api.py / templates/today.html
```

The board build takes ~30s (lots of live calls) and is cached for
`BOARD_CACHE_TTL` seconds (default 900).

## Notes / tuning ideas

- **FanGraphs is avoided on purpose** — pybaseball's FanGraphs scraper currently
  returns HTTP 403. Everything rides on Savant, BRef, and StatsAPI.
- Park HR factors and field orientations in `parks.py` are approximate — swap in
  your preferred values.
- Retractable roofs are scored as open (we can't know the roof state pre-game)
  and flagged in the game card; domes are neutralized.
- Possible next steps: batter barrel%/xISO as its own signal, platoon (L/R)
  splits, HR-prop odds overlay, a `proj`-vs-`posted` auto-refresh.

## Deploy (Render, free tier)

Push to GitHub, then in Render: **New + → Blueprint** and point it at the repo.
`render.yaml` provisions a free Python web service running
`gunicorn wsgi:app`.
