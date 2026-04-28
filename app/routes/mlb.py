import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.services.mlb import get_roster, get_schedule, get_upcoming, get_lineup, get_last_lineup
from app.services.trades import get_trades, add_trade, reset_trades
from app.services.predict import predict_game
from app.services.predict import load_weights, save_weights, DEFAULT_HIT_WEIGHTS, DEFAULT_PITCH_WEIGHTS, DEFAULT_BALANCE
from app.services import tracking
from app.services import backtest as bt

router = APIRouter()

@router.get("/roster/{team_code}")
def roster(team_code: str):
    return get_roster(team_code)

@router.get("/schedule/{team_code}")
def schedule(team_code: str, date: str = None):
    return get_schedule(team_code, date)

@router.get("/upcoming/{team_code}")
def upcoming(team_code: str, days: int = 7):
    return get_upcoming(team_code, days)

@router.get("/trades")
def trades():
    return get_trades()

@router.post("/trades")
async def trade(request: Request):
    payload = await request.json()
    return add_trade(payload)

@router.delete("/trades")
def reset():
    return reset_trades()

@router.get("/lineup/{game_id}")
def lineup(game_id: int):
    return get_lineup(game_id)

@router.get("/hitters/{team_code}")
def hitters(team_code: str):
    result = get_roster(team_code)
    if "error" in result:
        return result
    hitters = [p for p in result["roster"] if p["position_type"] != "Pitcher" and p["status"] == "Active"]
    return {"team": team_code, "hitters": hitters}

@router.get("/last-lineup/{team_code}")
def last_lineup(team_code: str):
    return get_last_lineup(team_code)

@router.get("/weights")
def get_weights():
    return load_weights()

@router.post("/weights")
async def update_weights(request: Request):
    payload = await request.json()
    save_weights(payload)
    return {"status": "saved"}

@router.post("/weights/reset")
def reset_weights():
    weights = {
        "hit_weights": DEFAULT_HIT_WEIGHTS,
        "pitch_weights": DEFAULT_PITCH_WEIGHTS,
        "balance": DEFAULT_BALANCE
    }
    save_weights(weights)
    return {"status": "reset"}

@router.get("/bullpen/{team_code}")
def bullpen(team_code: str):
    from app.services.stats import get_bullpen_era
    result = get_roster(team_code)
    if "error" in result:
        return result
    team_id = result["team_id"]
    era = get_bullpen_era(team_id)
    return {"team": team_code, "bullpen_era": era}

@router.get("/form/{team_code}")
def form(team_code: str):
    from app.services.stats import get_recent_form
    result = get_roster(team_code)
    if "error" in result:
        return result
    team_id = result["team_id"]
    return get_recent_form(team_id)

def _predict_for_game(home_team, away_team, game_id=None, game_date=None,
                      manual_home_lineup=None, manual_away_lineup=None,
                      log=True):
    """Reusable prediction core. Returns the prediction dict (or {'error': ...})."""
    home_result = get_roster(home_team)
    away_result = get_roster(away_team)
    if "error" in home_result:
        return {"error": f"Could not find roster for {home_team}"}
    if "error" in away_result:
        return {"error": f"Could not find roster for {away_team}"}

    home_roster = home_result["roster"]
    away_roster = away_result["roster"]
    home_pitcher_id = 0
    away_pitcher_id = 0
    lineup_source = "roster"

    if game_id:
        lineup_data = get_lineup(game_id)
        home_lineup = lineup_data.get("home", {})
        away_lineup = lineup_data.get("away", {})
        if home_lineup.get("lineup_available") and away_lineup.get("lineup_available"):
            lineup_source = "mlb_api"
            home_pitcher_id = home_lineup.get("starter_id", 0)
            away_pitcher_id = away_lineup.get("starter_id", 0)
            home_lineup_ids = {p["id"] for p in home_lineup["lineup"]}
            away_lineup_ids = {p["id"] for p in away_lineup["lineup"]}
            home_roster = [p for p in home_roster if p["id"] in home_lineup_ids]
            away_roster = [p for p in away_roster if p["id"] in away_lineup_ids]

    if manual_home_lineup:
        lineup_source = "manual"
        home_roster = manual_home_lineup
    if manual_away_lineup:
        lineup_source = "manual"
        away_roster = manual_away_lineup

    if not home_pitcher_id or not away_pitcher_id:
        sched = get_schedule(home_team, game_date)
        for g in sched.get("games", []):
            opp_code = g.get("opponent_code", "")
            if opp_code == away_team or away_team in g.get("opponent", ""):
                our_pn = g.get("our_probable_pitcher", "")
                opp_pn = g.get("opponent_probable_pitcher", "")
                for p in home_result["roster"]:
                    if p["name"] == our_pn:
                        home_pitcher_id = p["id"]
                for p in away_result["roster"]:
                    if p["name"] == opp_pn:
                        away_pitcher_id = p["id"]

    print(f"Predicting: {away_team} @ {home_team} | Source: {lineup_source}")
    home_team_id = home_result.get("team_id", 0)
    away_team_id = away_result.get("team_id", 0)
    result = predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id,
                          home_team_id, away_team_id)
    result["lineup_source"] = lineup_source

    log_id = None
    if log and game_id:
        try:
            log_id = tracking.log_prediction(
                home_team=home_team, away_team=away_team, prediction=result,
                game_id=game_id, game_date=game_date,
                home_pitcher_id=home_pitcher_id, away_pitcher_id=away_pitcher_id,
                weights=load_weights(),
            )
        except Exception as e:
            print(f"Failed to log prediction: {e}")
    result["prediction_id"] = log_id
    return result


@router.post("/predict")
async def predict(request: Request):
    try:
        payload = await request.json()
        return await asyncio.to_thread(
            _predict_for_game,
            payload["home_team"],
            payload["away_team"],
            payload.get("game_id"),
            payload.get("game_date"),
            payload.get("manual_home_lineup"),
            payload.get("manual_away_lineup"),
            not payload.get("skip_log", False),
        )
    except Exception as e:
        import traceback
        print("PREDICT ERROR:", traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/predict/today")
async def predict_today():
    """Fetch today's MLB schedule and predict every game."""
    import requests
    from datetime import date as _date
    today = _date.today().strftime("%Y-%m-%d")
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}"
           f"&hydrate=team,probablePitcher")
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

    # Bound concurrency so we don't hammer the MLB API
    sem = asyncio.Semaphore(4)

    async def run_one(g):
        async with sem:
            try:
                pred = await asyncio.to_thread(
                    _predict_for_game,
                    g["home_team"], g["away_team"],
                    g["game_id"], today, None, None, True
                )
                return {**g, **pred}
            except Exception as e:
                return {**g, "error": str(e)}

    results = await asyncio.gather(*[run_one(g) for g in schedule])
    return {"date": today, "games": results}


# ---------- prediction tracking ----------

@router.get("/predictions")
def predictions_list(status: str = None, limit: int = 200, game_date: str = None):
    return {"predictions": tracking.list_predictions(status=status, limit=limit, game_date=game_date)}


@router.get("/predictions/summary")
def predictions_summary():
    return tracking.summary()


@router.get("/predictions/dates")
def predictions_dates():
    return {"dates": tracking.available_dates()}


@router.post("/predictions/grade")
def predictions_grade():
    return tracking.grade_pending()


@router.delete("/predictions/{pred_id}")
def predictions_delete(pred_id: int):
    return tracking.delete_prediction(pred_id)


@router.delete("/predictions")
def predictions_reset():
    return tracking.reset_all()


@router.post("/predictions/dedupe")
def predictions_dedupe():
    """Clean up duplicate rows from before the dedup fix."""
    return tracking.dedupe_existing()


# ---------- backtest + tune ----------

@router.post("/backtest")
async def backtest(request: Request):
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
    # Run in a worker thread so the event loop stays free for other requests
    return await asyncio.to_thread(bt.run_backtest, start, end)


@router.post("/tune")
async def tune(request: Request):
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
    n_iter = int(payload.get("n_iter", 200))
    apply = bool(payload.get("apply", False))
    seed = int(payload.get("seed", 42))
    # Validate date range so we don't silently accept reversed inputs
    if start and end and start > end:
        return JSONResponse(
            {"error": f"start_date ({start}) must be before end_date ({end})"},
            status_code=400,
        )
    return await asyncio.to_thread(
        bt.run_tune, start, end, n_iter, apply, seed
    )


@router.post("/tune/clear-cache")
def tune_clear_cache():
    return bt.clear_cache()


@router.get("/auto-predict/status")
def auto_predict_status():
    from app.services.scheduler import state
    return state


@router.post("/auto-predict/run-now")
async def auto_predict_run_now():
    from app.services.scheduler import run_auto_predict_sync
    predicted, skipped = await asyncio.to_thread(run_auto_predict_sync)
    return {"predicted": predicted, "skipped": skipped}


@router.post("/tune-from-history")
async def tune_from_history(request: Request):
    """Tune weights using the user's own graded predictions as the test set."""
    payload = await request.json() if (await request.body()) else {}
    n_iter = int(payload.get("n_iter", 200))
    apply = bool(payload.get("apply", False))
    seed = int(payload.get("seed", 42))
    min_games = int(payload.get("min_games", 20))
    return await asyncio.to_thread(
        bt.run_tune_from_history, n_iter, apply, seed, min_games
    )