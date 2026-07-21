"""App configuration, driven by environment variables (see .env.example)."""
import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-not-secret")

    # How long (seconds) a built board is reused before we recompute it.
    # The board pulls a lot of live data, so a few minutes of caching keeps
    # page loads fast without going stale.
    BOARD_CACHE_TTL = int(os.environ.get("BOARD_CACHE_TTL", "900"))

    # How many rows the leaderboard view shows by default.
    LEADERBOARD_LIMIT = int(os.environ.get("LEADERBOARD_LIMIT", "50"))
