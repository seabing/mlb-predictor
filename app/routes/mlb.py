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
    print("HOME TEAM:", home_team)
    print("AWAY TEAM:", away_team)
    home_pitcher_id = payload.get("home_pitcher_id", 0)
    away_pitcher_id = payload.get("away_pitcher_id", 0)
    home_result = get_roster(home_team)
    print("HOME RESULT KEYS:", list(home_result.keys()))
    away_result = get_roster(away_team)
    print("AWAY RESULT KEYS:", list(away_result.keys()))
    home_roster = home_result["roster"]
    away_roster = away_result["roster"]
    return predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id)