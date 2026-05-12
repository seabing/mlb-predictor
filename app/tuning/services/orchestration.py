"""Top-level entry points for tuning operations.

Three orchestrators glue `Backtester` + `RandomSearchTuner` + the
prediction history into the shapes the API routes hand back to the user:

  - run_backtest(start, end, weights)        — score current weights on a date range
  - run_tune(start, end, n_iter, apply, seed) — random search on a date range
  - run_tune_from_history(n_iter, apply, ...) — random search on the user's own
                                                graded predictions (preferred)
"""
from __future__ import annotations

from datetime import date as _date, timedelta

from app.mlb.teams import team_id
from app.predictions.services.tracking import prediction_store
from app.predictions.services.weights import weights_store
from app.tuning.services.backtester import Backtester
from app.tuning.services.tuner import RandomSearchTuner


# ---- date helpers ----

def last_n_days_from_today(n: int = 30) -> tuple[str, str]:
    """Last N days ending yesterday (today's games may not be Final yet)."""
    end = _date.today() - timedelta(days=1)
    start = end - timedelta(days=n - 1)
    return start.isoformat(), end.isoformat()


# ---- backtest ----

def run_backtest(
    start_date: str | None = None,
    end_date: str | None = None,
    weights: dict | None = None,
    backtester: Backtester | None = None,
) -> dict:
    if not start_date or not end_date:
        start_date, end_date = last_n_days_from_today(30)
    bt = backtester or Backtester()
    print(f"[backtest] {start_date} -> {end_date}")
    games = bt.fetch_finals(start_date, end_date)
    print(f"  {len(games)} finals")
    games = bt.fetch_lineups_for(games)
    print(f"  {len(games)} games with lineups")
    features = bt.precompute_features(games)
    print(f"  {len(features)} games scorable")
    if weights is None:
        weights = weights_store.load()
    metrics = bt.evaluate(features, weights)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "metrics": metrics,
        "weights_used": weights,
    }


# ---- tune ----

def run_tune(
    start_date: str | None = None,
    end_date: str | None = None,
    n_iter: int = 200,
    apply: bool = False,
    seed: int = 42,
) -> dict:
    if not start_date or not end_date:
        start_date, end_date = last_n_days_from_today(30)
    bt = Backtester()
    tuner = RandomSearchTuner(backtester=bt)
    print(f"[tune] {start_date} -> {end_date}, n_iter={n_iter}")
    games = bt.fetch_finals(start_date, end_date)
    games = bt.fetch_lineups_for(games)
    features = bt.precompute_features(games)
    print(f"  scoring {len(features)} games per iteration")
    result = tuner.search(features, n_iter=n_iter, seed=seed)
    result["start_date"] = start_date
    result["end_date"] = end_date
    result["games_evaluated"] = len(features)
    tuner.history_log.append(result)
    if apply:
        weights_store.save(result["best_weights"])
        result["applied"] = True
    return result


def run_tune_from_history(
    n_iter: int = 200,
    apply: bool = False,
    seed: int = 42,
    min_games: int = 20,
) -> dict:
    """Tune against the user's own graded predictions — the most honest signal.

    Refuses to run on too small a sample (would just memorize noise).
    """
    graded = [
        p for p in prediction_store.list(status="graded", limit=10000)
        if p.get("game_id") and p.get("home_score") is not None
        and p.get("away_score") is not None
    ]
    if len(graded) < min_games:
        return {
            "error": f"Need at least {min_games} graded predictions to tune; "
                     f"you have {len(graded)}. Predict more games and let them grade.",
            "graded_count": len(graded),
        }

    print(f"[tune-from-history] {len(graded)} graded predictions in DB")
    games: list[dict] = []
    skipped = 0
    for p in graded:
        home_id = team_id(p.get("home_team") or "")
        away_id = team_id(p.get("away_team") or "")
        if not home_id or not away_id:
            skipped += 1
            continue
        games.append({
            "game_id": p["game_id"],
            "date": p.get("game_date"),
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_score": p["home_score"],
            "away_score": p["away_score"],
        })
    print(f"[tune-from-history] {len(games)} mapped to team IDs ({skipped} skipped)")

    bt = Backtester()
    tuner = RandomSearchTuner(backtester=bt)
    games = bt.fetch_lineups_for(games)
    print(f"[tune-from-history] {len(games)} games with lineups available")
    features = bt.precompute_features(games)
    print(f"[tune-from-history] {len(features)} games scorable")

    if len(features) < min_games:
        return {
            "error": f"Could only build features for {len(features)} games "
                     f"(need {min_games}). Try predicting more recent games.",
            "scorable_games": len(features),
        }

    result = tuner.search(features, n_iter=n_iter, seed=seed)
    result["games_evaluated"] = len(features)
    result["source"] = "graded_history"
    tuner.history_log.append(result)
    if apply:
        weights_store.save(result["best_weights"])
        result["applied"] = True
    return result


def clear_cache() -> dict:
    """Wipe the feature cache. Routed via /api/tune/clear-cache."""
    return Backtester().cache.clear()
