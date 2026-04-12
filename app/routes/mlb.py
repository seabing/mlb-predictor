from fastapi import APIRouter, Request
from app.services.mlb import get_roster, get_schedule, get_upcoming
from app.services.trades import get_trades, add_trade, reset_trades
from app.services.predict import predict_game

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

@router.post("/predict")
async def predict(request: Request):
    payload = await request.json()
    home_team = payload["home_team"]
    away_team = payload["away_team"]
    game_date = payload.get("game_date", None)

    home_roster = get_roster(home_team)["roster"]
    away_roster = get_roster(away_team)["roster"]

    # Try to get probable pitchers from schedule
    home_pitcher_id = 0
    away_pitcher_id = 0
    schedule = get_schedule(home_team, game_date)
    for game in schedule.get("games", []):
        opp_code = game.get("opponent_code", "")
        if opp_code == away_team or away_team in game.get("opponent", ""):
            # Find pitcher IDs from roster
            our_pitcher_name = game.get("our_probable_pitcher", "")
            opp_pitcher_name = game.get("opponent_probable_pitcher", "")
            for p in home_roster:
                if p["name"] == our_pitcher_name:
                    home_pitcher_id = p["id"]
            for p in away_roster:
                if p["name"] == opp_pitcher_name:
                    away_pitcher_id = p["id"]

    return predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id)