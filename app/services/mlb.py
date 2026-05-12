"""Deprecated shim — re-exports moved to app.mlb.

Other features still import from this path; the refactor will migrate them
in their own steps. New code should import directly from app.mlb.
"""
from __future__ import annotations

from app.mlb.client import client as _client
from app.mlb.teams import TEAM_IDS  # noqa: F401 — re-exported

BASE_URL = _client.base_url


def get_roster(team_code: str):
    return _client.get_roster(team_code)


def get_schedule(team_code: str, date: str | None = None):
    return _client.get_schedule(team_code, date)


def get_upcoming(team_code: str, days: int = 7):
    return _client.get_upcoming(team_code, days)


def get_lineup(game_id: int):
    return _client.get_lineup(game_id)


def get_last_lineup(team_code: str):
    return _client.get_last_lineup(team_code)
