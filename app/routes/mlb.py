from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.services.mlb import get_roster, get_schedule
from app.services.trades import get_trades, add_trade, reset_trades
from app.services.mlb import get_roster, get_schedule, get_upcoming

router = APIRouter()

@router.get("/roster/{team_code}")
def roster(team_code: str):
    return get_roster(team_code)

@router.get("/schedule/{team_code}")
def schedule(team_code: str, date: str = None):
    return get_schedule(team_code, date)

@router.get("/trades")
def trades():
    return get_trades()

@router.post("/trades")
def trade(payload: dict):
    return add_trade(payload)

@router.delete("/trades")
def reset():
    return reset_trades()

@router.get("/upcoming/{team_code}")
def upcoming(team_code: str, days: int = 7):
    return get_upcoming(team_code, days)