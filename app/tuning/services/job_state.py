"""In-memory job state for the Backtest & Tune background job.

A single BacktestJobState singleton is imported by orchestration (to write
progress) and by routes (to read it for the polling endpoint). It is
intentionally in-process only — on server restart the state resets to idle,
which is fine.

Thread safety: all mutations go through update() / log() / start() /
finish() / fail(), each of which holds a lock for the duration.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class BacktestJobState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._reset()

    def _reset(self) -> None:
        self._data = {
            "status": "idle",       # idle | running | done | error
            "phase": "",            # human-readable current phase name
            "phase_detail": "",     # e.g. "Fetching 2025-05-10"
            "progress": 0,          # 0-100
            "started_at": None,
            "elapsed_seconds": None,
            "result": None,
            "error": None,
            "log": [],              # recent messages (capped)
        }

    # ------------------------------------------------------------------ #
    # Mutation helpers                                                      #
    # ------------------------------------------------------------------ #

    def start(self) -> bool:
        """Mark job as started. Returns False if already running."""
        with self._lock:
            if self._data["status"] == "running":
                return False
            self._reset()
            self._data["status"] = "running"
            self._data["started_at"] = time.time()
            return True

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                self._data[k] = v
            self._data["elapsed_seconds"] = (
                round(time.time() - self._data["started_at"], 1)
                if self._data.get("started_at") else None
            )

    def log(self, message: str) -> None:
        with self._lock:
            self._data["log"].append(message)
            if len(self._data["log"]) > 150:
                self._data["log"] = self._data["log"][-150:]

    def finish(self, result: dict) -> None:
        with self._lock:
            self._data["status"] = "done"
            self._data["progress"] = 100
            self._data["result"] = result
            self._data["elapsed_seconds"] = (
                round(time.time() - self._data["started_at"], 1)
                if self._data.get("started_at") else None
            )

    def fail(self, error: str) -> None:
        with self._lock:
            self._data["status"] = "error"
            self._data["error"] = error
            self._data["elapsed_seconds"] = (
                round(time.time() - self._data["started_at"], 1)
                if self._data.get("started_at") else None
            )

    # ------------------------------------------------------------------ #
    # Read                                                                  #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._data["status"] == "running"


# Module-level singleton
job_state = BacktestJobState()
