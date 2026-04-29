"""Background scheduler that auto-predicts upcoming games.

Every CHECK_INTERVAL_SECONDS, scan today + tomorrow's MLB schedule and
predict any game whose first pitch is within PREDICT_WINDOW_MIN, as long
as no prediction already exists for that game_id.

Designed for FastAPI's lifespan. The dedupe logic in
tracking.log_prediction guarantees we never log the same game twice even
if this runs concurrently with a manual /predict call.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import requests

CHECK_INTERVAL_SECONDS = int(os.getenv("AUTO_PREDICT_INTERVAL_SECONDS", 2 * 60 * 60))
# Window must be >= interval / 60 or games slip between checks. Default window
# slightly exceeds the interval so every game gets at least one chance.
PREDICT_WINDOW_MIN = int(os.getenv("AUTO_PREDICT_WINDOW_MIN", 150))
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# Public state — exposed via /api/auto-predict/status
state = {
    "running": False,
    "interval_seconds": CHECK_INTERVAL_SECONDS,
    "window_minutes": PREDICT_WINDOW_MIN,
    "last_run_at": None,
    "last_run_predicted": 0,
    "last_run_skipped": 0,
    "last_run_error": None,
    "next_run_at": None,
    "total_predicted": 0,
}


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def run_auto_predict_sync():
    """Run one scheduler pass synchronously. Returns (predicted, skipped)."""
    # Lazy imports to avoid circular dependencies at module load
    from app.routes.mlb import _predict_for_game
    from app.services.tracking import _conn, init_db

    now = _utcnow()
    today = now.date()
    dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    predicted = 0
    skipped = 0
    init_db()

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
                status_state = game["status"].get("detailedState", "")
                # Don't predict games that are already in progress or over
                if status_state in ("Final", "Game Over", "Completed Early",
                                    "In Progress", "Manager challenge",
                                    "Delayed Start"):
                    continue

                game_time_iso = game.get("gameDate", "")
                if not game_time_iso:
                    continue
                try:
                    game_time = datetime.fromisoformat(
                        game_time_iso.replace("Z", "+00:00")
                    )
                except Exception:
                    continue

                minutes_until = (game_time - now).total_seconds() / 60.0
                if minutes_until < 0 or minutes_until > PREDICT_WINDOW_MIN:
                    continue

                game_id = game.get("gamePk")
                if not game_id:
                    continue

                # Skip if we already have a row for this game
                with _conn() as c:
                    row = c.execute(
                        "SELECT id FROM predictions WHERE game_id = ?",
                        (game_id,),
                    ).fetchone()
                if row:
                    skipped += 1
                    continue

                home = (game["teams"]["home"]["team"]
                        .get("abbreviation", "") or "").upper()
                away = (game["teams"]["away"]["team"]
                        .get("abbreviation", "") or "").upper()
                if not home or not away:
                    continue

                try:
                    _predict_for_game(home, away, game_id, d)
                    predicted += 1
                    print(f"[auto-predict] {away} @ {home} "
                          f"(starts in {int(minutes_until)} min)")
                except Exception as e:
                    print(f"[auto-predict] predict failed for "
                          f"{away}@{home} ({game_id}): {e}")

    return predicted, skipped


async def auto_predict_loop():
    """Long-running task. Started by FastAPI lifespan."""
    print(f"[auto-predict] loop started "
          f"(every {CHECK_INTERVAL_SECONDS}s, window {PREDICT_WINDOW_MIN}m)")
    state["running"] = True
    # Small startup delay so first run doesn't race with app initialization
    await asyncio.sleep(30)
    while True:
        run_at = _utcnow().isoformat()
        try:
            predicted, skipped = await asyncio.to_thread(run_auto_predict_sync)
            state["last_run_at"] = run_at
            state["last_run_predicted"] = predicted
            state["last_run_skipped"] = skipped
            state["last_run_error"] = None
            state["total_predicted"] += predicted
            print(f"[auto-predict] pass complete: "
                  f"{predicted} predicted, {skipped} skipped (already logged)")
        except asyncio.CancelledError:
            print("[auto-predict] loop cancelled")
            state["running"] = False
            raise
        except Exception as e:
            state["last_run_error"] = str(e)
            print(f"[auto-predict] pass error: {e}")
        next_run = _utcnow() + timedelta(seconds=CHECK_INTERVAL_SECONDS)
        state["next_run_at"] = next_run.isoformat()
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            state["running"] = False
            raise
