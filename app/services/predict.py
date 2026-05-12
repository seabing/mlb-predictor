"""Deprecated shim — predict feature moved to app.predictions.

Kept for back-compat with backtest.py and scheduler.py during refactor.
New code should import from app.predictions.
"""
from __future__ import annotations

from app.predictions.park_factors import PARK_FACTORS  # noqa: F401
from app.predictions.services.engine import prediction_engine as _engine
from app.predictions.services.weights import (  # noqa: F401
    DEFAULT_BALANCE,
    DEFAULT_HIT_WEIGHTS,
    DEFAULT_PITCH_WEIGHTS,
    HIT_RANGES,
    PITCH_RANGES,
    normalize,
    weights_store as _weights_store,
)

WEIGHTS_FILE = _weights_store.path


def load_weights():
    return _weights_store.load()


def save_weights(weights):
    _weights_store.save(weights)


def predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id,
                 home_team_id=0, away_team_id=0):
    return _engine.predict_game(
        home_roster, away_roster,
        home_pitcher_id, away_pitcher_id,
        home_team_id, away_team_id,
    )


def score_lineup(roster, pitcher_id=0, weights=None, split="home"):
    # Used by callers that wanted the lower-level helper. Reach into the
    # engine's private method — acceptable for the transition.
    return _engine._score_lineup(
        roster, pitcher_id,
        weights or _weights_store.load(),
        split,
    )


def score_pitcher(player_id, weights=None):
    return _engine._score_pitcher(
        player_id,
        weights or _weights_store.load(),
    )
