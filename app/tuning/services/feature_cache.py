"""Disk-backed feature cache for backtests/tunes.

Backtests re-use the same lineup + per-player normalized stats across many
weight evaluations, and across multiple tune runs. Caching this is the
difference between a 15-second second run and another 15-minute first run.

The cache is a flat dict serialized to JSON at `settings.backtest_cache_path`.
Keys are namespaced strings:

    finals::{start}::{end}    -> list of Final games (legacy one-off calls)
    box::{game_id}            -> boxscore lineup data
    hit::{player_id}::{split} -> normalized hitting features
    pitch::{player_id}        -> normalized pitching features
    bvp::{batter}::{pitcher}  -> normalized batter-vs-pitcher features
    bullpen::{team_id}        -> team bullpen ERA

Incremental-mode keys (used by run_backtest_and_tune):

    date_done::{date}         -> True once a date has been fully processed
    date_games::{date}        -> list of game_ids that have stored features
    feat::{game_id}           -> complete precomputed feature dict for a game
"""
from __future__ import annotations

import json
import os
from datetime import date as _date, timedelta
from typing import Any

from app.core.config import settings


class FeatureCache:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or settings.backtest_cache_path
        self._cache: dict[str, Any] = self._load()
        self._dirty = False

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self) -> None:
        """Persist if anything has changed since the last save."""
        if not self._dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._cache, f)
        self._dirty = False

    def clear(self) -> dict:
        if os.path.exists(self.path):
            os.remove(self.path)
        self._cache = {}
        self._dirty = False
        return {"status": "cleared"}

    def has(self, key: str) -> bool:
        return key in self._cache

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        self._dirty = True

    # ---- Incremental date tracking ----

    def date_is_done(self, date: str) -> bool:
        """True if this date has been fully fetched and features stored."""
        return bool(self._cache.get(f"date_done::{date}"))

    def mark_date_done(self, date: str, game_ids: list) -> None:
        """Record that a date is fully processed and store its game id list."""
        self._cache[f"date_done::{date}"] = True
        self._cache[f"date_games::{date}"] = game_ids
        self._dirty = True

    def store_features(self, game_id, features: dict) -> None:
        """Persist a fully-computed feature dict for a single game."""
        self._cache[f"feat::{game_id}"] = features
        self._dirty = True

    def get_all_features_in_range(self, start_date: str, end_date: str) -> list:
        """Return every stored feature dict whose date falls in [start, end]."""
        start = _date.fromisoformat(start_date)
        end = _date.fromisoformat(end_date)
        cur = start
        features = []
        while cur <= end:
            d = cur.isoformat()
            game_ids = self._cache.get(f"date_games::{d}", [])
            for gid in game_ids:
                feat = self._cache.get(f"feat::{gid}")
                if feat is not None:
                    features.append(feat)
            cur += timedelta(days=1)
        return features

    def missing_dates_in_range(self, start_date: str, end_date: str) -> list:
        """Return dates in [start, end] that have not yet been processed."""
        start = _date.fromisoformat(start_date)
        end = _date.fromisoformat(end_date)
        cur = start
        missing = []
        while cur <= end:
            d = cur.isoformat()
            if not self.date_is_done(d):
                missing.append(d)
            cur += timedelta(days=1)
        return missing

    def cached_date_count(self, start_date: str, end_date: str) -> int:
        """Count how many dates in the range are already cached."""
        start = _date.fromisoformat(start_date)
        end = _date.fromisoformat(end_date)
        cur = start
        count = 0
        while cur <= end:
            if self.date_is_done(cur.isoformat()):
                count += 1
            cur += timedelta(days=1)
        return count
