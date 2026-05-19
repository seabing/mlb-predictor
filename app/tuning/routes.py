"""HTTP routes for tuning: backtest, tune, tune-from-history, and the new
combined run-all endpoint with a polling /status endpoint.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.tuning.services import orchestration as _orch
from app.tuning.services.job_state import job_state

router = APIRouter()


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
    retu