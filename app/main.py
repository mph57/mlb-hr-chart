"""HTML routes: the daily home-run board."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, current_app, render_template, request

from . import cache
from .scoring import get_board

main_bp = Blueprint("main", __name__)


def _build(date: str):
    ttl = current_app.config["BOARD_CACHE_TTL"]
    board = cache.get_or_build(f"board:{date}", ttl, lambda: get_board(date))
    # Stamp build time here (scoring stays clock-free for testability).
    if board.get("generated_at") is None:
        board = {**board, "generated_at": dt.datetime.now().isoformat(timespec="seconds")}
    return board


@main_bp.get("/")
@main_bp.get("/today")
def today():
    date = request.args.get("date") or dt.date.today().isoformat()
    limit = int(request.args.get("limit", current_app.config["LEADERBOARD_LIMIT"]))
    sort = request.args.get("sort", "score")  # "score" | "edge"
    board = _build(date)

    rows = list(board["leaderboard"])
    if sort == "edge":
        # Priced bats first, ranked by edge; unpriced bats fall to the bottom.
        rows.sort(key=lambda r: (r["odds"] is not None,
                                 r["odds"]["edge_pp"] if r.get("odds") else 0,
                                 r["hr_score"]), reverse=True)
    board = {**board, "leaderboard": rows}
    return render_template("today.html", board=board, limit=limit,
                           date=date, sort=sort)
