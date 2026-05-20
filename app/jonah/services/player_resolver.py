"""Resolve a player name to an MLB player record (id + position).

The codebase had no name-based lookup — only get_roster by team. To "trade for"
a named player we need their MLB Stats API person id and position so the rating
engine can pull their stats. We fetch the full active-player list for a season
(one call, then cached in memory) and fuzzy-match the name. The returned dict is
shaped to match the roster dicts the engine consumes.
"""
from __future__ import annotations

import difflib

import requests

from app.mlb.client import client as mlb_client

# season -> {"by_name": {lower_name: player_dict}, "names": [fullName, ...]}
_CACHE: dict[int, dict] = {}
DEFAULT_SEASON = 2026


def _load_season(season: int) -> dict:
    if season in _CACHE:
        return _CACHE[season]
    url = f"{mlb_client.base_url}/sports/1/players"
    try:
        resp = mlb_client.session.get(
            url, params={"season": season}, timeout=mlb_client.timeout
        ).json()
    except Exception as e:
        print(f"[player_resolver] failed to load players for {season}: {e}")
        return {"by_name": {}, "names": []}

    by_name: dict[str, dict] = {}
    names: list[str] = []
    for raw in resp.get("people", []):
        full = raw.get("fullName", "")
        if not full:
            continue
        pos = raw.get("primaryPosition", {})
        by_name[full.lower()] = {
            "id": raw["id"],
            "name": full,
            "position": pos.get("abbreviation", ""),
            "position_type": pos.get("type", ""),
            "status": "Active",
            "resolved": True,
        }
        names.append(full)
    cache = {"by_name": by_name, "names": names}
    _CACHE[season] = cache
    return cache


def resolve(name: str, season: int = DEFAULT_SEASON) -> dict | None:
    """Return a roster-shaped player dict for `name`, or None if not found."""
    if not name or not name.strip():
        return None
    cache = _load_season(season)
    by_name = cache["by_name"]
    key = name.strip().lower()

    if key in by_name:
        return dict(by_name[key])

    # Fuzzy match against full names.
    close = difflib.get_close_matches(name.strip(), cache["names"], n=1, cutoff=0.8)
    if close:
        return dict(by_name[close[0].lower()])

    # Last resort: substring (e.g. partial last name).
    for full_lower, player in by_name.items():
        if key in full_lower:
            return dict(player)
    return None
