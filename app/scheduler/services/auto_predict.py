"""Background auto-predict scheduler.

Every `interval_seconds`, scan today + tomorrow's MLB schedule and predict
any game whose first pitch is within `window_minutes`, as long as no
prediction already exists for that game_id. Grades any pending predictions
at the start of each pass.

Lifecycle: `scheduler.run_loop()` is wrapped in an asyncio task by main.py's
lifespan. The same instance exposes `run_once()` for the manual /run-now
endpoint and a `state` dict for /status.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import requests

from app.core.config import settings
from app.predictions.services.predict_one import predict_one_game
from app.predictions.services.tracking import prediction_store

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# Game statuses we never (re-)predict during a scheduler pass.
SKIP_STATUSES = (
    "Final", "Game Over", "Completed Early",
    "In Progress", "Manager challenge", "Delayed Start",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class AutoPredictScheduler:
    """Owns the scheduler state + the per-pass logic.

    Externally visible state is held in `self.state` (mutated in place) so
    the /status endpoint can read it without holding a reference to a
    snapshot. There is one process-wide singleton at module bottom.
    """

    STARTUP_DELAY_SECONDS = 30

    def __init__(
        self,
        interval_seconds: int | None = None,
        window_minutes: int | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds or settings.auto_predict_interval_seconds
        self.window_minutes = window_minutes or settings.auto_predict_window_minutes
        self.state: dict = {
            "running": False,
            "interval_seconds": self.interval_seconds,
            "window_minutes": self.window_minutes,
            "last_run_at": None,
            "last_run_predicted": 0,
            "last_run_skipped": 0,
            "last_run_error": None,
            "next_run_at": None,
            "total_predicted": 0,
        }

    # ---- one pass ----

    def run_once(self) -> tuple[int, int]:
        """Synchronous: one scheduler pass. Returns (predicted, skipped)."""
        # Grade first — pulls in any games that finished since last pass.
        try:
            graded_info = prediction_store.grade_pending()
            if graded_info.get("graded"):
                print(f"[auto-predict] graded {graded_info['graded']} pending predictions")
        except Exception as e:
            print(f"[auto-predict] grade_pending failed: {e}")

        now = _utcnow()
        today = now.date()
        dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
        predicted = 0
        skipped = 0

        for d in dates:
            try:
                resp = requests.get(
                    SCHEDULE_URL,
                    params={"sportId": 1, "date": d, "hydrate": "team,probablePitcher"},
                    timeout=15,
                ).json()
            except Exception as e:
                print(f"[auto-predict] schedule fetch failed for {d}: {e}")
                continue

            for date_entry in resp.get("dates", []):
                for game in date_entry.get("games", []):
                    p, s = self._maybe_predict_game(game, d, now)
                    predicted += p
                    skipped += s
        return predicted, skipped

    def _maybe_predict_game(self, game: dict, date_iso: str, now: datetime) -> tuple[int, int]:
        """Return (predicted, skipped) — 1 of each based on outcome."""
        status_state = game["status"].get("detailedState", "")
        if status_state in SKIP_STATUSES:
            return 0, 0

        game_time_iso = game.get("gameDate", "")
        if not game_time_iso:
            return 0, 0
        try:
            game_time = datetime.fromisoformat(game_time_iso.replace("Z", "+00:00"))
        except Exception:
            return 0, 0

        minutes_until = (game_time - now).total_seconds() / 60.0
        if minutes_until < 0 or minutes_until > self.window_minutes:
            return 0, 0

        game_id = game.get("gamePk")
        if not game_id:
            return 0, 0

        # Skip if we already have a row for this game
        if prediction_store.get_by_game_id(game_id):
            return 0, 1

        home = (game["teams"]["home"]["team"].get("abbreviation", "") or "").upper()
        away = (game["teams"]["away"]["team"].get("abbreviation", "") or "").upper()
        if not home or not away:
            return 0, 0

        try:
            predict_one_game(home, away, game_id, date_iso)
            print(f"[auto-predict] {away} @ {home} (starts in {int(minutes_until)} min)")
            return 1, 0
        except Exception as e:
            print(f"[auto-predict] predict failed for {away}@{home} ({game_id}): {e}")
            return 0, 0

    # ---- loop ----

    async def run_loop(self) -> None:
        """Long-running asyncio task. Started by FastAPI lifespan."""
        print(
            f"[auto-predict] loop started "
            f"(every {self.interval_seconds}s, window {self.window_minutes}m)"
        )
        self.state["running"] = True
        await asyncio.sleep(self.STARTUP_DELAY_SECONDS)
        while True:
            run_at = _utcnow().isoformat()
            try:
                predicted, skipped = await asyncio.to_thread(self.run_once)
                self.state["last_run_at"] = run_at
                self.state["last_run_predicted"] = predicted
                self.state["last_run_skipped"] = skipped
                self.state["last_run_error"] = None
                self.state["total_predicted"] += predicted
                print(
                    f"[auto-predict] pass complete: "
                    f"{predicted} predicted, {skipped} skipped (already logged)"
                )
            except asyncio.CancelledError:
                print("[auto-predict] loop cancelled")
                self.state["running"] = False
                raise
            except Exception as e:
                self.state["last_run_error"] = str(e)
                print(f"[auto-predict] pass error: {e}")
            self.state["next_run_at"] = (
                _utcnow() + timedelta(seconds=self.interval_seconds)
            ).isoformat()
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                self.state["running"] = False
                raise


# Module-level singleton — main.py wires this into lifespan.
scheduler = AutoPredictScheduler()
