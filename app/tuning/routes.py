"""HTTP routes for tuning: backtest, tune (date-range), tune-from-history."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.tuning.services import orchestration as _orch

router = APIRouter()


@router.post("/backtest")
async def backtest(request: Request):
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
    # Heavy work — run in a worker thread so the event loop stays free.
    return await asyncio.to_thread(_orch.run_backtest, start, end)


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
    return await asyncio.to_thread(_orch.run_tune, start, end, n_iter, apply, seed)


@router.post("/tune/clear-cache")
def tune_clear_cache():
    return _orch.clear_cache()


@router.post("/tune-from-history")
async def tune_from_history(request: Request):
    payload = await request.json() if (await request.body()) else {}
    n_iter = int(payload.get("n_iter", 200))
    apply = bool(payload.get("apply", False))
    seed = int(payload.get("seed", 42))
    min_games = int(payload.get("min_games", 20))
    return await asyncio.to_thread(
        _orch.run_tune_from_history, n_iter, apply, seed, min_games
    )
