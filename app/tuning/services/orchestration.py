"""Top-level entry points for tuning operations.

Four orchestrators glue Backtester + RandomSearchTuner + the
prediction history into the shapes the API routes hand back to the user:

  run_backtest(start, end, weights)         -- score current weights on a date range
  run_tune(start, end, n_iter, apply, seed) -- random search on a date range
  run_tune_from_history(n_iter, apply, ...) -- random search on graded predictions
  run_backtest_and_tune(...)                -- incremental combined run w/ progress
"""
from __future__ import annotations

from datetime import date as _date, timedelta

from app.mlb.teams import team_id
from app.predictions.services.tracking import prediction_store
from app.predictions.services.weights import weights_store
from app.tuning.services.backtester import Backtester
from app.tuning.services.tuner import RandomSearchTuner


def last_n_days_from_today(n=30):
    end = _date.today() - timedelta(days=1)
    start = end - timedelta(days=n - 1)
    return start.isoformat(), end.isoformat()


def _dates_in_range(start, end):
    cur = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    dates = []
    while cur <= end_d:
        dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def _summarize_weight_changes(before, after):
    changes = []
    for group in ("hit_weights", "pitch_weights", "balance"):
        b = before.get(group, {})
        a = after.get(group, {})
        for key in sorted(set(list(b.keys()) + list(a.keys()))):
            old = b.get(key, 0)
            new = a.get(key, 0)
            delta = new - old
            if abs(delta) >= 0.005:
                changes.append({
                    "group": group,
                    "key": key,
                    "old": round(old, 4),
                    "new": round(new, 4),
                    "delta": round(delta, 4),
                })
    changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return changes


def run_backtest(start_date=None, end_date=None, weights=None, backtester=None):
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


def run_tune(start_date=None, end_date=None, n_iter=200, apply=False, seed=42):
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


def run_tune_from_history(n_iter=200, apply=False, seed=42, min_games=20):
    graded = [
        p for p in prediction_store.list(status="graded", limit=10000)
        if p.get("game_id") and p.get("home_score") is not None
        and p.get("away_score") is not None
    ]
    if len(graded) < min_games:
        return {
            "error": (
                f"Need at least {min_games} graded predictions to tune; "
                f"you have {len(graded)}. Predict more games and let them grade."
            ),
            "graded_count": len(graded),
        }

    print(f"[tune-from-history] {len(graded)} graded predictions in DB")
    games = []
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
    print(f"[tune-from-history] {len(games)} mapped ({skipped} skipped)")

    bt = Backtester()
    tuner = RandomSearchTuner(backtester=bt)
    games = bt.fetch_lineups_for(games)
    features = bt.precompute_features(games)

    if len(features) < min_games:
        return {
            "error": (
                f"Could only build features for {len(features)} games "
                f"(need {min_games}). Try predicting more recent games."
            ),
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


def clear_cache():
    return Backtester().cache.clear()


def run_backtest_and_tune(start_date=None, end_date=None, n_iter=200, apply=False, seed=42):
    """Incrementally fetch missing dates, score baseline, tune, return summary.

    Progress is written to job_state so the /status endpoint can stream it
    to the UI. Designed to run in a background thread.
    """
    from app.tuning.services.job_state import job_state as state

    try:
        if not start_date or not end_date:
            start_date, end_date = last_n_days_from_today(30)

        bt = Backtester()
        cache = bt.cache

        all_dates = _dates_in_range(start_date, end_date)
        missing = cache.missing_dates_in_range(start_date, end_date)
        n_cached = len(all_dates) - len(missing)

        state.update(
            phase="Fetching game data",
            phase_detail=f"{n_cached} dates already cached, fetching {len(missing)} new",
            progress=2,
        )
        state.log(
            f"Range: {start_date} to {end_date} "
            f"({len(all_dates)} days, {n_cached} cached, {len(missing)} to fetch)"
        )

        for i, date in enumerate(missing):
            pct = 2 + int((i / max(len(missing), 1)) * 58)
            state.update(
                phase="Fetching game data",
                phase_detail=f"{date}  ({i + 1} of {len(missing)})",
                progress=pct,
            )
            try:
                games = bt.fetch_finals(date, date)
                if not games:
                    cache.mark_date_done(date, [])
                    cache.save()
                    state.log(f"  {date}: no final games")
                    continue

                games_with_lineups = bt.fetch_lineups_for(games)
                features = bt.precompute_features(games_with_lineups)

                game_ids = []
                for feat in features:
                    cache.store_features(feat["game_id"], feat)
                    game_ids.append(feat["game_id"])
                cache.mark_date_done(date, game_ids)
                cache.save()
                state.log(
                    f"  {date}: {len(games)} finals, "
                    f"{len(games_with_lineups)} with lineups, "
                    f"{len(features)} scorable"
                )
            except Exception as e:
                state.log(f"  {date}: error - {e}")
                cache.mark_date_done(date, [])
                cache.save()

        state.update(
            phase="Scoring baseline",
            phase_detail="Collecting cached features...",
            progress=62,
        )
        all_features = cache.get_all_features_in_range(start_date, end_date)
        state.log(f"Total scorable games in range: {len(all_features)}")

        if not all_features:
            result = {
                "error": (
                    "No scorable games found in date range. "
                    "Try a wider range or wait for games to finish."
                ),
                "start_date": start_date,
                "end_date": end_date,
            }
            state.finish(result)
            return result

        weights_before = weights_store.load()
        baseline = bt.evaluate(all_features, weights_before)
        state.log(
            f"Baseline: log_loss={baseline['log_loss']}  "
            f"accuracy={baseline['accuracy']}  n={baseline['n']}"
        )

        state.update(
            phase="Tuning",
            phase_detail=f"Running {n_iter} random-search iterations...",
            progress=65,
        )
        tuner = RandomSearchTuner(backtester=bt)
        tune_result = tuner.search(
            all_features, n_iter=n_iter, seed=seed, base_weights=weights_before
        )
        best_weights = tune_result["best_weights"]
        best_metrics = tune_result["best_metrics"]

        applied = False
        if apply:
            weights_store.save(best_weights)
            applied = True
            state.log("New weights applied to the model.")

        tuner.history_log.append({
            **tune_result,
            "start_date": start_date,
            "end_date": end_date,
            "source": "run_all",
        })

        weight_changes = _summarize_weight_changes(weights_before, best_weights)
        ll_delta = (
            round(best_metrics["log_loss"] - baseline["log_loss"], 4)
            if baseline["log_loss"] is not None and best_metrics["log_loss"] is not None
            else None
        )
        acc_delta = (
            round(best_metrics["accuracy"] - baseline["accuracy"], 4)
            if baseline["accuracy"] is not None and best_metrics["accuracy"] is not None
            else None
        )

        state.log(
            f"Done: log_loss {baseline['log_loss']} -> {best_metrics['log_loss']}  "
            f"accuracy {baseline['accuracy']} -> {best_metrics['accuracy']}"
        )

        result = {
            "start_date": start_date,
            "end_date": end_date,
            "games_evaluated": len(all_features),
            "new_dates_fetched": len(missing),
            "cached_dates_used": n_cached,
            "baseline_metrics": baseline,
            "best_metrics": best_metrics,
            "ll_delta": ll_delta,
            "acc_delta": acc_delta,
            "weight_changes": weight_changes,
            "applied": applied,
            "iterations": n_iter,
        }
        state.finish(result)
        return result

    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[run_backtest_and_tune] ERROR: {msg}")
        try:
            from app.tuning.services.job_state import job_state as state
            state.fail(msg)
        except Exception:
            pass
        raise


def warm_cache_for_yesterday():
    """Fetch and cache yesterday's games if not already done."""
    from datetime import date as _d
    yesterday = (_d.today() - timedelta(days=1)).isoformat()
    bt = Backtester()
    if bt.cache.date_is_done(yesterday):
        return
    try:
        games = bt.fetch_finals(yesterday, yesterday)
        if not games:
            bt.cache.mark_date_done(yesterday, [])
            bt.cache.save()
            return
        games = bt.fetch_lineups_for(games)
        features = bt.precompute_features(games)
        game_ids = []
        for feat in features:
            bt.cache.store_features(feat["game_id"], feat)
            game_ids.append(feat["game_id"])
        bt.cache.mark_date_done(yesterday, game_ids)
        bt.cache.save()
        print(f"[cache-warmer] {yesterday}: cached {len(features)} games")
    except Exception as e:
        print(f"[cache-warmer] {yesterday}: failed - {e}")
