"""HTTP routes for tuning: backtest, tune, tune-from-history, the combined
run-all endpoint with a polling /status endpoint, and natural-language tuning.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.llm import LLMError
from app.tuning.services import nl_tuner as _nl
from app.tuning.services import orchestration as _orch
from app.tuning.services.job_state import job_state

router = APIRouter()


# ------------------------------------------------------------------ #
# Natural-language model tuning (Item 6)                               #
# ------------------------------------------------------------------ #

@router.post("/tune/nl")
async def tune_nl(request: Request):
    """Interpret a plain-English tuning request and return updated weights.

    Request body: {"message": "<plain English>", "current_weights": {...}}
    Response:     {"weights": {...}, "summary": "..."}

    Values are validated and clamped to the slider ranges. The caller decides
    whether/when to persist them via the existing POST /api/weights.
    """
    payload = await request.json() if (await request.body()) else {}
    message = payload.get("message", "")
    current_weights = payload.get("current_weights")

    try:
        result = await asyncio.to_thread(_nl.nl_tune, message, current_weights)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    return result


# ------------------------------------------------------------------ #
# Combined incremental Backtest + Tune                                 #
# ------------------------------------------------------------------ #

@router.post("/tune/run-all")
async def run_all(request: Request):
    """Kick off a background Backtest + Tune job.

    Returns 409 immediately if a job is already running.
    The caller should poll GET /api/tune/status for progress.
    """
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
    n_iter = int(payload.get("n_iter", 200))
    apply = bool(payload.get("apply", False))
    seed = int(payload.get("seed", 42))

    if not job_state.start():
        return JSONResponse({"error": "A job is already running."}, status_code=409)

    async def _run():
        try:
            await asyncio.to_thread(
                _orch.run_backtest_and_tune, start, end, n_iter, apply, seed
            )
        except Exception as e:
            snap = job_state.snapshot()
            if snap.get("status") not in ("done", "error"):
                job_state.fail(str(e))

    asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/tune/status")
def tune_status():
    """Return the current job state snapshot for UI polling."""
    return job_state.snapshot()


# ------------------------------------------------------------------ #
# Legacy individual endpoints (kept for compatibility)                 #
# ------------------------------------------------------------------ #

@router.post("/backtest")
async def backtest(request: Request):
    payload = await request.json() if (await request.body()) else {}
    start = payload.get("start_date")
    end = payload.get("end_date")
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
