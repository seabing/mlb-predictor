"""Deprecated shim — tracking moved to app.predictions.services.tracking.

Kept for backward compat with scheduler.py and any other callers during
the refactor. New code should use `prediction_store` directly.
"""
from __future__ import annotations

from app.core.config import settings
from app.predictions.services.tracking import prediction_store as _store

DB_PATH = settings.predictions_db_path


def init_db():
    _store.init()


def log_prediction(home_team, away_team, prediction, game_id=None, game_date=None,
                   home_pitcher_id=0, away_pitcher_id=0, weights=None,
                   force_replace=False):
    return _store.log(
        home_team=home_team,
        away_team=away_team,
        prediction=prediction,
        game_id=game_id,
        game_date=game_date,
        home_pitcher_id=home_pitcher_id,
        away_pitcher_id=away_pitcher_id,
        weights=weights,
        force_replace=force_replace,
    )


def get_by_game_id(game_id):
    return _store.get_by_game_id(game_id)


def dedupe_existing():
    return _store.dedupe()


def grade_pending(limit=200):
    return _store.grade_pending(limit=limit)


def list_predictions(status=None, limit=200, game_date=None):
    return _store.list(status=status, limit=limit, game_date=game_date)


def available_dates(limit=90):
    return _store.available_dates(limit=limit)


def summary():
    return _store.summary()


def delete_prediction(pred_id):
    return _store.delete(pred_id)


def reset_all():
    return _store.reset_all()


# The legacy module also exposed _conn() — used by scheduler.run_auto_predict_sync
# to check "does this game_id already exist?". Mirror that here.

def _conn():
    _store.init()
    return _store.connect()
