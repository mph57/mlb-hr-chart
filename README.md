# ⚾ MLB Daily Home Run Board

A Flask web app that ranks every hitter on today's MLB slate by a blended
**HR Score**, built from the factors that actually drive home runs:

| Signal | Weight | Source |
|---|---|---|
| **Weather** — game-time temp + wind blowing out/in (per park orientation) | 12% | Open-Meteo |
| **Ballpark** — multi-year HR park factor | 13% | static table (`app/data/parks.py`) |
| **Pitcher** — opposing starter's fly-ball % and hard-hit % allowed | 22% | Baseball Savant (Statcast) |
| **Batter form** — last-7-day SLG + HR rate | 23% | Baseball-Reference |
| **Platoon** — batter's power (SLG + HR rate) vs the opposing starter's **hand** (L/R split); bumps hitters who mash that hand | 18% | MLB StatsAPI |
| **BvP** — batter's career line vs today's starter (sample-damped) | 12% | MLB StatsAPI |

The platoon split handles switch hitters automatically (their vs-L / vs-R lines
already reflect the advantaged side) and is disk-cached to `instance/cache/`
so the ~1 call/batter happens once per season, not on every board build.

Each signal is mapped to a 0–100 sub-score (50 = league-neutral), then blended.
Small samples (recent PA, career AB vs pitcher) regress toward neutral so a
2-for-3 fluke can't dominate. Weights live in `WEIGHTS` in `app/scoring.py` —
tune them however you like.

> Model output only — **not betting advice.**

## HR prop odds column

The board adds an **HR Odds** column showing each batter's "to hit a home run"
(Over 0.5) prop line next to their HR Score — no model/edge math, just the price.
Set the source with `ODDS_PROVIDER`:

**`actionnetwork` (default, free, no key)** — pulls today's HR props from Action
Network's public JSON API (`web/v2/games/{id}/props`, `core_bet_type_33_hr`)
across all games and shows the **Consensus** line (market average). Set
`AN_BOOK_ID` for a specific book (49 Caesars, 69 FanDuel, 30 Open). Matches
players by team + initial/last. Runs fine from this Mac; a hosted box *may* get
rate-limited (it's their internal endpoint), so keep `file` as a backup.

**`file` (free)** — drop a CSV or JSON at `instance/odds/<date>.csv`:

```csv
player,book,over
Kyle Schwarber,DK,+300
Hunter Goodman,DK,+330
```

Names are matched fuzzily (accents and Jr./Sr. suffixes ignored). See
`instance/odds/sample_odds.csv`.

**`theoddsapi`** — set `ODDS_PROVIDER=theoddsapi` and `ODDS_API_KEY=...` (paid
Business plan for the `batter_home_runs` market). Shows the best Over price
across books.

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
    odds.py       pluggable HR-prop providers (Action Network / file / Odds API)
  scoring.py      blends the five signals + attaches each bat's HR prop line
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
