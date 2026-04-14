from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.services.mlb import get_roster, get_schedule, get_upcoming, get_lineup, get_last_lineup
from app.services.trades import get_trades, add_trade, reset_trades
from app.services.predict import predict_game
from app.services.predict import load_weights, save_weights, DEFAULT_HIT_WEIGHTS, DEFAULT_PITCH_WEIGHTS, DEFAULT_BALANCE

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

@router.post("/predict")
async def predict(request: Request):
    try:
        payload = await request.json()
        home_team = payload["home_team"]
        away_team = payload["away_team"]
        game_date = payload.get("game_date", None)
        game_id = payload.get("game_id", None)
        manual_home_lineup = payload.get("manual_home_lineup", None)
        manual_away_lineup = payload.get("manual_away_lineup", None)

        home_result = get_roster(home_team)
        away_result = get_roster(away_team)

        if "error" in home_result:
            return JSONResponse({"error": f"Could not find roster for {home_team}"}, status_code=400)
        if "error" in away_result:
            return JSONResponse({"error": f"Could not find roster for {away_team}"}, status_code=400)

        home_roster = home_result["roster"]
        away_roster = away_result["roster"]

        home_pitcher_id = 0
        away_pitcher_id = 0
        lineup_source = "roster"

        # Try to get actual lineup from MLB API
        if game_id:
            lineup_data = get_lineup(game_id)
            home_lineup = lineup_data.get("home", {})
            away_lineup = lineup_data.get("away", {})

            if home_lineup.get("lineup_available") and away_lineup.get("lineup_available"):
                lineup_source = "mlb_api"
                home_pitcher_id = home_lineup.get("starter_id", 0)
                away_pitcher_id = away_lineup.get("starter_id", 0)

                # Build roster-like objects from lineup
                home_lineup_ids = {p["id"] for p in home_lineup["lineup"]}
                away_lineup_ids = {p["id"] for p in away_lineup["lineup"]}
                home_roster = [p for p in home_roster if p["id"] in home_lineup_ids]
                away_roster = [p for p in away_roster if p["id"] in away_lineup_ids]

        # Manual lineup overrides everything
        if manual_home_lineup:
            lineup_source = "manual"
            home_roster = manual_home_lineup
        if manual_away_lineup:
            lineup_source = "manual"
            away_roster = manual_away_lineup

        # Fall back to schedule-based pitcher lookup if still no pitcher
        if not home_pitcher_id or not away_pitcher_id:
            schedule = get_schedule(home_team, game_date)
            for game in schedule.get("games", []):
                opp_code = game.get("opponent_code", "")
                if opp_code == away_team or away_team in game.get("opponent", ""):
                    our_pitcher_name = game.get("our_probable_pitcher", "")
                    opp_pitcher_name = game.get("opponent_probable_pitcher", "")
                    for p in home_result["roster"]:
                        if p["name"] == our_pitcher_name:
                            home_pitcher_id = p["id"]
                    for p in away_result["roster"]:
                        if p["name"] == opp_pitcher_name:
                            away_pitcher_id = p["id"]

        print(f"Predicting: {home_team} vs {away_team} | Source: {lineup_source}")
        print(f"Home pitcher: {home_pitcher_id}, Away pitcher: {away_pitcher_id}")

        home_team_id = get_roster(home_team).get("team_id", 0)
        result = predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id, home_team_id)
        result["lineup_source"] = lineup_source
        return result

    except Exception as e:
        import traceback
        print("PREDICT ERROR:", traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)