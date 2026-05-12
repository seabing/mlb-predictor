"""Legacy router — shrinking as features migrate to their own folders.

Still here:
  - /salaries/*           (moves to app/salaries/ later)
  - /trades, /trades/*    (moves to app/trades/ later)
  - /backtest, /tune,
    /tune-from-history,
    /tune/clear-cache     (moves to app/tuning/ in step 4)
  - /auto-predict/*       (moves to app/scheduler/ in step 5)

Once empty, this file goes away.
"""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services import backtest as bt
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


# ---------- backtest + tune ----------

@router.post("/backtest")
async def backtest(request: Request):
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
    return await asyncio.to_thread(bt.run_backtest, start, end)


@router.post("/tune")
async def tune(request: Request):
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
    n_iter = int(payload.get("n_iter", 200))
    apply = bool(payload.get("apply", False))
    seed = int(payload.get("seed", 42))
    if start and end and start > end:
        return JSONResponse(
            {"error": f"start_date ({start}) must be before end_date ({end})"},
            status_code=400,
        )
    return await asyncio.to_thread(bt.run_tune, start, end, n_iter, apply, seed)


@router.post("/tune/clear-cache")
def tune_clear_cache():
    return bt.clear_cache()


@router.post("/tune-from-history")
async def tune_from_history(request: Request):
    payload = await request.json() if (await request.body()) else {}
    n_iter = int(payload.get("n_iter", 200))
    apply = bool(payload.get("apply", False))
    seed = int(payload.get("seed", 42))
    min_games = int(payload.get("min_games", 20))
    return await asyncio.to_thread(
        bt.run_tune_from_history, n_iter, apply, seed, min_games
    )


# ---------- auto-predict scheduler ----------

@router.get("/auto-predict/status")
def auto_predict_status():
    from app.services.scheduler import state
    return state


@router.post("/auto-predict/run-now")
async def auto_predict_run_now():
    from app.services.scheduler import run_auto_predict_sync
    predicted, skipped = await asyncio.to_thread(run_auto_predict_sync)
    return {"predicted": predicted, "skipped": skipped}
