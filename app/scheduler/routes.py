"""Routes exposing the auto-predict scheduler: /status, /run-now."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.scheduler.services.auto_predict import scheduler

router = APIRouter()


@router.get("/auto-predict/status")
def auto_predict_status():
    return scheduler.state


@router.post("/auto-predict/run-now")
async def auto_predict_run_now():
    predicted, skipped = await asyncio.to_thread(scheduler.run_once)
    return {"predicted": predicted, "skipped": skipped}
