from fastapi import APIRouter
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
def trade(payload: dict):
    return add_trade(payload)

@router.delete("/trades")
def reset():
    return reset_trades()

@router.post("/predict")
def predict(payload: dict):
    home_roster = payload["home_roster"]
    away_roster = payload["away_roster"]
    home_pitcher_id = payload["home_pitcher_id"]
    away_pitcher_id = payload["away_pitcher_id"]
    return predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id)