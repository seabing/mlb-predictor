"""Deprecated shim — auto-predict moved to app.scheduler.

Re-exported for back-compat; this module will be deleted at the end of
the refactor. New code should use app.scheduler.services.auto_predict.
"""
from __future__ import annotations

from app.scheduler.services.auto_predict import scheduler as _scheduler

state = _scheduler.state


async def auto_predict_loop() -> None:
    await _scheduler.run_loop()


def run_auto_predict_sync() -> tuple[int, int]:
    return _scheduler.run_once()
