"""Static ballpark reference data.

For each of the 30 MLB venues we keep:
  - ``hr_factor``  : multi-year home-run park factor, 100 = league average.
                     >100 favors home runs, <100 suppresses them. These are
                     approximate recent-year values; tune them as you like.
  - ``lat``/``lon``: venue coordinates, used to pull game-time weather.
  - ``roof``       : "open", "retractable", or "dome". Domes (and closed
                     retractables) neutralize weather entirely.
  - ``cf_azimuth`` : compass bearing (degrees, 0=N, 90=E) from home plate
                     toward center field. Lets us decide whether a given wind
                     is blowing *out* (toward CF, helps HR) or *in*.

Keyed by MLB team abbreviation. ``VENUE_BY_ID`` maps StatsAPI venue ids to
these records so the schedule module can look parks up directly.
"""
from __future__ import annotations

PARKS: dict[str, dict] = {
    "ARI": {"name": "Chase Field",            "hr_factor": 103, "lat": 33.4455, "lon": -112.0667, "roof": "retractable", "cf_azimuth": 0},
    "ATL": {"name": "Truist Park",            "hr_factor": 104, "lat": 33.8908, "lon": -84.4678,  "roof": "open",        "cf_azimuth": 25},
    "BAL": {"name": "Camden Yards",           "hr_factor": 99,  "lat": 39.2839, "lon": -76.6217,  "roof": "open",        "cf_azimuth": 33},
    "BOS": {"name": "Fenway Park",            "hr_factor": 102, "lat": 42.3467, "lon": -71.0972,  "roof": "open",        "cf_azimuth": 45},
    "CHC": {"name": "Wrigley Field",          "hr_factor": 101, "lat": 41.9484, "lon": -87.6553,  "roof": "open",        "cf_azimuth": 27},
    "CWS": {"name": "Rate Field",             "hr_factor": 103, "lat": 41.8300, "lon": -87.6338,  "roof": "open",        "cf_azimuth": 8},
    "CIN": {"name": "Great American Ball Park","hr_factor": 116, "lat": 39.0975, "lon": -84.5069,  "roof": "open",        "cf_azimuth": 40},
    "CLE": {"name": "Progressive Field",      "hr_factor": 98,  "lat": 41.4962, "lon": -81.6852,  "roof": "open",        "cf_azimuth": 0},
    "COL": {"name": "Coors Field",            "hr_factor": 112, "lat": 39.7559, "lon": -104.9942, "roof": "open",        "cf_azimuth": 0},
    "DET": {"name": "Comerica Park",          "hr_factor": 96,  "lat": 42.3390, "lon": -83.0485,  "roof": "open",        "cf_azimuth": 25},
    "HOU": {"name": "Daikin Park",            "hr_factor": 106, "lat": 29.7572, "lon": -95.3556,  "roof": "retractable", "cf_azimuth": 20},
    "KC":  {"name": "Kauffman Stadium",       "hr_factor": 95,  "lat": 39.0517, "lon": -94.4803,  "roof": "open",        "cf_azimuth": 0},
    "LAA": {"name": "Angel Stadium",          "hr_factor": 100, "lat": 33.8003, "lon": -117.8827, "roof": "open",        "cf_azimuth": 27},
    "LAD": {"name": "Dodger Stadium",         "hr_factor": 103, "lat": 34.0739, "lon": -118.2400, "roof": "open",        "cf_azimuth": 25},
    "MIA": {"name": "loanDepot park",         "hr_factor": 98,  "lat": 25.7781, "lon": -80.2197,  "roof": "retractable", "cf_azimuth": 40},
    "MIL": {"name": "American Family Field",  "hr_factor": 106, "lat": 43.0280, "lon": -87.9712,  "roof": "retractable", "cf_azimuth": 8},
    "MIN": {"name": "Target Field",           "hr_factor": 100, "lat": 44.9817, "lon": -93.2776,  "roof": "open",        "cf_azimuth": 22},
    "NYM": {"name": "Citi Field",             "hr_factor": 97,  "lat": 40.7571, "lon": -73.8458,  "roof": "open",        "cf_azimuth": 25},
    "NYY": {"name": "Yankee Stadium",         "hr_factor": 111, "lat": 40.8296, "lon": -73.9262,  "roof": "open",        "cf_azimuth": 20},
    "OAK": {"name": "Sutter Health Park",     "hr_factor": 102, "lat": 38.5804, "lon": -121.5133, "roof": "open",        "cf_azimuth": 30},
    "PHI": {"name": "Citizens Bank Park",     "hr_factor": 107, "lat": 39.9061, "lon": -75.1665,  "roof": "open",        "cf_azimuth": 10},
    "PIT": {"name": "PNC Park",               "hr_factor": 97,  "lat": 40.4469, "lon": -80.0057,  "roof": "open",        "cf_azimuth": 60},
    "SD":  {"name": "Petco Park",             "hr_factor": 95,  "lat": 32.7073, "lon": -117.1566, "roof": "open",        "cf_azimuth": 0},
    "SF":  {"name": "Oracle Park",            "hr_factor": 91,  "lat": 37.7786, "lon": -122.3893, "roof": "open",        "cf_azimuth": 65},
    "SEA": {"name": "T-Mobile Park",          "hr_factor": 95,  "lat": 47.5914, "lon": -122.3325, "roof": "retractable", "cf_azimuth": 0},
    "STL": {"name": "Busch Stadium",          "hr_factor": 98,  "lat": 38.6226, "lon": -90.1928,  "roof": "open",        "cf_azimuth": 0},
    "TB":  {"name": "George M. Steinbrenner Field", "hr_factor": 101, "lat": 27.9797, "lon": -82.5073, "roof": "open",   "cf_azimuth": 45},
    "TEX": {"name": "Globe Life Field",       "hr_factor": 101, "lat": 32.7473, "lon": -97.0847,  "roof": "retractable", "cf_azimuth": 0},
    "TOR": {"name": "Rogers Centre",          "hr_factor": 105, "lat": 43.6414, "lon": -79.3894,  "roof": "retractable", "cf_azimuth": 0},
    "WSH": {"name": "Nationals Park",         "hr_factor": 101, "lat": 38.8730, "lon": -77.0074,  "roof": "open",        "cf_azimuth": 30},
}

# Map full venue names (as StatsAPI reports them) -> team abbreviation, so the
# schedule module can resolve a game's park regardless of home team.
VENUE_TO_ABBR: dict[str, str] = {p["name"]: abbr for abbr, p in PARKS.items()}

# League-average HR factor fallback for unknown venues (neutral doubleheader
# sites, relocations, international series, etc.).
NEUTRAL_PARK = {"name": "Unknown Venue", "hr_factor": 100, "lat": None, "lon": None,
                "roof": "open", "cf_azimuth": 0}


def get_park(abbr: str | None = None, venue_name: str | None = None) -> dict:
    """Resolve a park record by team abbreviation or venue name."""
    if abbr and abbr in PARKS:
        return PARKS[abbr]
    if venue_name and venue_name in VENUE_TO_ABBR:
        return PARKS[VENUE_TO_ABBR[venue_name]]
    return NEUTRAL_PARK
