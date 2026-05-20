"""Roster operations — pure functions that add/drop players on a roster list.

A "roster" here is the same list-of-dicts shape the MLB client returns and the
prediction engine consumes: each player has at least id, name, position,
position_type, status. These functions never do I/O — name resolution and stat
lookups happen elsewhere (player_resolver). Keeping this pure makes it trivial
to unit-test the move logic.
"""
from __future__ import annotations

import difflib


def find_player(roster: list[dict], name: str) -> dict | None:
    """Case-insensitive, fuzzy match of a name to a player on the roster."""
    if not name:
        return None
    name_l = name.strip().lower()
    # Exact (case-insensitive) first.
    for p in roster:
        if p.get("name", "").lower() == name_l:
            return p
    # Substring (e.g. "Judge" -> "Aaron Judge").
    for p in roster:
        if name_l in p.get("name", "").lower():
            return p
    # Fuzzy fallback.
    names = [p.get("name", "") for p in roster]
    close = difflib.get_close_matches(name, names, n=1, cutoff=0.8)
    if close:
        for p in roster:
            if p.get("name") == close[0]:
                return p
    return None


def drop_player(roster: list[dict], name: str) -> tuple[list[dict], dict | None]:
    """Return (new_roster, dropped_player_or_None). Does not mutate the input."""
    target = find_player(roster, name)
    if target is None:
        return list(roster), None
    new_roster = [p for p in roster if p is not target]
    return new_roster, target


def add_player(roster: list[dict], player: dict) -> list[dict]:
    """Append a resolved player dict. Skips if the id is already present."""
    if any(p.get("id") == player.get("id") for p in roster):
        return list(roster)
    return list(roster) + [player]


def apply_move(
    roster: list[dict],
    adds: list[dict] | None = None,
    drops: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """Apply a set of adds (resolved player dicts) and drops (names).

    Returns (new_roster, report) where report records what actually happened so
    the caller can explain it to the user (including names that weren't found).
    """
    report = {"added": [], "dropped": [], "drop_not_found": []}
    new_roster = list(roster)

    for name in (drops or []):
        new_roster, dropped = drop_player(new_roster, name)
        if dropped:
            report["dropped"].append(dropped.get("name", name))
        else:
            report["drop_not_found"].append(name)

    for player in (adds or []):
        new_roster = add_player(new_roster, player)
        report["added"].append(player.get("name", "unknown"))

    return new_roster, report
