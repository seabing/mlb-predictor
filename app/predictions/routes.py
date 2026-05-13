"""HTTP routes for the predictions feature.

Covers:
  - POST /predict, GET /predict/today           — make predictions
  - GET /predictions, /predictions/dates,
    /predictions/summary, POST /predictions/grade,
    /predictions/dedupe, /predictions/backfill,
    DELETE /predictions[/{id}]                  — tracking + history
  - GET /weights, POST /weights, /weights/reset — tunable coefficients
"""
from __future__ import annotations

import asyncio
import requests
from datetime import date as _date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.predictions.services.backfill import last_n_days_range, run_backfill
from app.predictions.services.confidence import confidence_calculator
from app.predictions.services.predict_one import predict_one_game
from app.predictions.services.tracking import prediction_store
from app.predictions.services.weights import (
    DEFAULT_BALANCE,
    DEFAULT_HIT_WEIGHTS,
    DEFAULT_PITCH_WEIGHTS,
    default_weights_dict,
    weights_store,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

@router.get("/weights")
def get_weights():
    return weights_store.load()


@router.post("/weights")
async def update_weights(request: Request):
    payload = await request.json()
    weights_store.save(payload)
    return {"status": "saved"}


@router.post("/weights/reset")
def reset_weights():
    weights_store.save(default_weights_dict())
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Predictions (make)
# ---------------------------------------------------------------------------

@router.post("/predict")
async def predict(request: Request):
    try:
        payload = await request.json()
        return await asyncio.to_thread(
            predict_one_game,
            payload["home_team"],
            payload["away_team"],
            payload.get("game_id"),
            payload.get("game_date"),
            payload.get("manual_home_lineup"),
            payload.get("manual_away_lineup"),
            not payload.get("skip_log", False),
            bool(payload.get("force", False)),
        )
    except Exception as e:
        import traceback
        print("PREDICT ERROR:", traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/predict/today")
async def predict_today():
    """Fetch today's MLB schedule and predict every game.

    Strategy: if we already have a prediction logged for a game, return it
    as-is (preserves the original prediction even after the game has
    started). Only run a fresh predict for games that haven't been logged
    AND haven't started.
    """
    today = _date.today().strftime("%Y-%m-%d")
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={today}&hydrate=team,probablePitcher"
    )
    try:
        resp = requests.get(url, timeout=15).json()
    except Exception as e:
        return JSONResponse({"error": f"Schedule fetch failed: {e}"}, status_code=500)

    schedule = []
    for date_entry in resp.get("dates", []):
        for game in date_entry.get("games", []):
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            schedule.append({
                "game_id": game["gamePk"],
                "date": today,
                "home_team": home["team"].get("abbreviation", ""),
                "away_team": away["team"].get("abbreviation", ""),
                "home_team_name": home["team"]["name"],
                "away_team_name": away["team"]["name"],
                "home_pitcher": home.get("probablePitcher", {}).get("fullName", "TBD"),
                "away_pitcher": away.get("probablePitcher", {}).get("fullName", "TBD"),
                "status": game["status"]["detailedState"],
                "venue": game.get("venue", {}).get("name", ""),
                "game_time": game.get("gameDate", ""),
            })

    if not schedule:
        return {"date": today, "games": []}

    # Grade any pending predictions first so finished games show their result.
    try:
        await asyncio.to_thread(prediction_store.grade_pending)
    except Exception as e:
        print(f"[predict_today] grade_pending failed: {e}")

    in_progress = {
        "In Progress", "Manager challenge", "Delayed",
        "Delayed Start", "Warmup", "Pre-Game",
    }
    finals = {"Final", "Game Over", "Completed Early"}
    sem = asyncio.Semaphore(4)

    async def run_one(g):
        async with sem:
            existing = prediction_store.get_by_game_id(g["game_id"])
            if existing:
                out = {
                    **g,
                    "home_win_pct": existing["home_win_pct"],
                    "away_win_pct": existing["away_win_pct"],
                    "predicted_winner": existing["predicted_winner"],
                    "lineup_source": existing["lineup_source"] or "logged",
                    "prediction_id": existing["id"],
                    "status_logged": existing["status"],
                    "actual_winner": existing.get("actual_winner"),
                    "home_score": existing.get("home_score"),
                    "away_score": existing.get("away_score"),
                }
                confidence_calculator.annotate(
                    out,
                    lineup_source=existing.get("lineup_source") or "logged",
                    home_team=existing.get("home_team"),
                    away_team=existing.get("away_team"),
                    home_pitcher_id=existing.get("home_pitcher_id") or 0,
                    away_pitcher_id=existing.get("away_pitcher_id") or 0,
                )
                return out
            if g["status"] in in_progress or g["status"] in finals:
                return {**g, "skipped_reason": f"no prediction logged before {g['status']}"}
            try:
                pred = await asyncio.to_thread(
                    predict_one_game,
                    g["home_team"], g["away_team"], g["game_id"], today,
                    None, None, True, False,
                )
                return {**g, **pred}
            except Exception as e:
                return {**g, "error": str(e)}

    results = await asyncio.gather(*[run_one(g) for g in schedule])
    return {"date": today, "games": results}


# ---------------------------------------------------------------------------
# Predictions (read / manage)
# ---------------------------------------------------------------------------

@router.get("/predictions")
def predictions_list(status: str | None = None, limit: int = 200,
                     game_date: str | None = None):
    rows = prediction_store.list(status=status, limit=limit, game_date=game_date)
    for p in rows:
        confidence_calculator.annotate(p)
    return {"predictions": rows}


@router.get("/predictions/summary")
def predictions_summary():
    return prediction_store.summary()


@router.get("/predictions/dates")
def predictions_dates():
    return {"dates": prediction_store.available_dates()}


@router.get("/predictions/calibration")
def predictions_calibration(bucket_width: float = 0.05):
    return prediction_store.calibration(bucket_width=bucket_width)


@router.post("/predictions/grade")
def predictions_grade():
    return prediction_store.grade_pending()


@router.delete("/predictions/{pred_id}")
def predictions_delete(pred_id: int):
    return prediction_store.delete(pred_id)


@router.delete("/predictions")
def predictions_reset():
    return prediction_store.reset_all()


@router.post("/predictions/dedupe")
def predictions_dedupe():
    return prediction_store.dedupe()


@router.post("/predictions/backfill")
async def predictions_backfill(request: Request):
    payload = await request.json() if (await request.body()) else {}
    days = int(payload.get("days") or 0)
    start = payload.get("start_date")
    end = payload.get("end_date")
    if days and not start and not end:
        start, end = last_n_days_range(days)
    if not start or not end:
        return JSONResponse(
            {"error": "Provide start_date+end_date or days"}, status_code=400,
        )
    return await asyncio.to_thread(run_backfill, start, end)
