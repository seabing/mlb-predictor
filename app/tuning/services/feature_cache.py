"""Disk-backed feature cache for backtests/tunes.

Backtests re-use the same lineup + per-player normalized stats across many
weight evaluations, and across multiple tune runs. Caching this is the
difference between a 15-second second run and another 15-minute first run.

The cache is a flat dict serialized to JSON at `settings.backtest_cache_path`.
Keys are namespaced strings:

    finals::{start}::{end}    -> list of Final games
    box::{game_id}            -> boxscore lineup data
    hit::{player_id}::{split} -> normalized hitting features
    pitch::{player_id}        -> normalized pitching features
    bvp::{batter}::{pitcher}  -> normalized batter-vs-pitcher features
    bullpen::{team_id}        -> team bullpen ERA
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.config import settings


class FeatureCache:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or settings.backtest_cache_path
        self._cache: dict[str, Any] = self._load()
        self._dirty = False

    # ---- I/O ----

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

    # ---- dict-ish access ----

    def has(self, key: str) -> bool:
        return key in self._cache

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        self._dirty = True
