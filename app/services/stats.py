"""Deprecated shim — re-exports moved to app.mlb.stats.

Other features still import from this path; the refactor will migrate them
in their own steps. New code should import directly from app.mlb.stats.
"""
from __future__ import annotations

from app.mlb.stats import (  # noqa: F401
    blend_hitting,
    blend_pitching,
    stats_service as _stats,
)

# Preserve module-level constants that callers reference
WEIGHT_2025 = _stats.WEIGHT_PRIOR
WEIGHT_2026 = _stats.WEIGHT_CURRENT
BASE_URL = _stats.client.base_url


def get_hitting_stats(player_id):
    return _stats.get_hitting_stats(player_id)


def get_pitching_stats(player_id):
    return _stats.get_pitching_stats(player_id)


def get_hitting_splits(player_id, split="home"):
    return _stats.get_hitting_splits(player_id, split)


def get_batter_vs_pitcher(batter_id, pitcher_id):
    return _stats.get_batter_vs_pitcher(batter_id, pitcher_id)


def get_bullpen_era(team_id):
    return _stats.get_bullpen_era(team_id)


def get_recent_form(team_id, games=10):
    return _stats.get_recent_form(team_id, games)
