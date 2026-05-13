"""Legacy router — shrinking as features migrate to their own folders.

Still here:
  - /salaries/*    (moves to app/salaries/ in step 6)
  - /trades, /trades/*  (moves to app/trades/ in step 6)

Once empty, this file goes away.
"""
from fastapi import APIRouter, Request

from app.services.trades import add_trade, get_trades, reset_trades

router = APIRouter()


# ---------- salaries ----------

@router.get("/salaries/status")
def salaries_status():
    from app.services.salaries import cache_status
    return cache_status()


@router.post("/salaries/refresh/{team_code}")
def salaries_refresh(team_code: str):
    from app.services.salaries import fetch_team_salaries
    players = fetch_team_salaries(team_code, force=True)
    return {"team": team_code.upper(), "player_count": len(players)}


@router.get("/salaries/debug/{team_code}")
def salaries_debug(team_code: str):
    from app.services.salaries import debug_team
    return debug_team(team_code)


@router.post("/salaries/clear-cache")
def salaries_clear():
    from app.services.salaries import clear_cache
    return clear_cache()


# ---------- trades ----------

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
