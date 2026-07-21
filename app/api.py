"""JSON API for the board (handy for cron jobs, notebooks, or a future UI)."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, current_app, jsonify, request

from . import cache
from .scoring import get_board

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.get("/board.json")
def board_json():
    date = request.args.get("date") or dt.date.today().isoformat()
    ttl = current_app.config["BOARD_CACHE_TTL"]
    board = cache.get_or_build(f"board:{date}", ttl, lambda: get_board(date))
    limit = request.args.get("limit", type=int)
    if limit:
        board = {**board, "leaderboard": board["leaderboard"][:limit]}
    return jsonify(board)


@api_bp.post("/refresh")
def refresh():
    """Force a rebuild for a date (drops the cache entry)."""
    date = request.args.get("date") or dt.date.today().isoformat()
    cache.invalidate(f"board:{date}")
    return jsonify({"invalidated": date})
