"""Backfill past Final games.

Walks the MLB schedule for a date range and runs the predictor for every
Final game we don't already have logged. Used as a one-shot tool from the
Predictions UI (or after a fresh deploy that wiped the DB).
"""
from __future__ import annotations

from datetime import date as _date, timedelta

import requests

from app.mlb.teams import code_for_id

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
FINAL_STATES = ("Final", "Game Over", "Completed Early")


def run_backfill(start_date: str, end_date: str) -> dict:
    """Re-create predictions for all Final games in [start_date, end_date].

    Existing rows (any status) are left alone — first-prediction-wins.
    After predicting, runs one grading pass to settle the new rows.
    """
    # Lazy imports avoid circular dependencies with routes
    from app.predictions.services.tracking import prediction_store
    from app.predictions.services.predict_one import predict_one_game

    try:
        resp = requests.get(
            MLB_SCHEDULE_URL,
            params={
                "sportId": 1,
                "startDate": start_date,
                "endDate": end_date,
                "gameType": "R",
            },
            timeout=30,
        ).json()
    except Exception as e:
        return {"error": f"Schedule fetch failed: {e}"}

    games: list[dict] = []
    for date_entry in resp.get("dates", []):
        for g in date_entry.get("games", []):
            if g["status"].get("detailedState") not in FINAL_STATES:
                continue
            home_id = g["teams"]["home"]["team"]["id"]
            away_id = g["teams"]["away"]["team"]["id"]
            home_code = code_for_id(home_id)
            away_code = code_for_id(away_id)
            if not home_code or not away_code:
                continue
            games.append({
                "game_id": g["gamePk"],
                "date": date_entry["date"],
                "home_team": home_code,
                "away_team": away_code,
            })

    print(f"[backfill] {start_date} -> {end_date}: {len(games)} Final games")
    predicted = 0
    already_existed = 0
    errors: list[dict] = []
    for g in games:
        if prediction_store.get_by_game_id(g["game_id"]):
            already_existed += 1
            continue
        try:
            predict_one_game(g["home_team"], g["away_team"], g["game_id"], g["date"])
            predicted += 1
            if predicted % 10 == 0:
                print(f"[backfill] {predicted}/{len(games)} predicted...")
        except Exception as e:
            errors.append({"game_id": g["game_id"], "error": str(e)})

    print(f"[backfill] grading {predicted} fresh predictions")
    grade = prediction_store.grade_pending(limit=2000)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "games_found": len(games),
        "newly_predicted": predicted,
        "already_existed": already_existed,
        "graded_now": grade.get("graded", 0),
        "errors": errors[:20],
    }


def last_n_days_range(days: int) -> tuple[str, str]:
    """Helper: yields (start, end) covering the last `days` days through yesterday."""
    end = _date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()
