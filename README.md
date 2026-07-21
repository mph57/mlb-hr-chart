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

- **Model %** — season HR/PA (regressed toward league, so low-PA bats don't
  spike) × gentle park/weather/pitcher context factors, compounded over the
  batter's expected plate appearances. Recency lives in the HR Score, *not*
  here, so a hot 3-for-12 week can't distort the price.
- **Edge** — Model % minus the market's *de-vigged* implied probability. When
  only the Over price is known (can't de-vig), a bet is flagged `VALUE` only if
  the edge clears `VALUE_THRESHOLD_PP` (default 3pp) so the book's hold isn't
  mistaken for an edge. **Fair** shows the model's own break-even line and
  **EV** the expected return per $1 on the Over.

Sort the board by **Edge vs line** (toolbar) to surface value plays.

### Feeding in odds

Odds require a source (no reliable free feed exists — DK/FanDuel block servers,
The Odds API gates player props behind a paid plan). Two providers, set by
`ODDS_PROVIDER`:

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
    odds.py       pluggable HR-prop providers (file / The Odds API) + de-vig
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
