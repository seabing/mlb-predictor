"""Team rating — a single, comparable win probability for a whole roster.

WHY THIS EXISTS
The existing prediction_engine predicts a *single game*: it needs two teams,
two starting pitchers, lineups and a park. Jonah's roster-move feature asks a
*team-level* question with no opponent ("what happens to the Yankees' win
probability if they trade for a starter?"). So we compute a team's strength from
its own players using the SAME normalize + weights machinery the engine uses,
and compare it to a league-average strength derived through those same weights.

Because the before/after comparison shares the league-average baseline, the
*delta* is meaningful even though the absolute number is an approximation. At a
perfectly league-average roster, the result is ~50%.

This module reuses the public stats helpers (blend_hitting/blend_pitching,
stats_service) and the weight ranges (HIT_RANGES/PITCH_RANGES/normalize) — it
does NOT reach into engine internals, so it stays decoupled from per-game logic.
"""
from __future__ import annotations

from app.mlb.stats import blend_hitting, blend_pitching, stats_service
from app.predictions.services.weights import (
    HIT_RANGES,
    PITCH_RANGES,
    normalize,
    weights_store,
)

# League-average stat lines (approximate, recent MLB). Used to derive the
# baseline strength through whatever weights are currently loaded, so an average
# roster lands near 50% regardless of how the sliders are set.
LEAGUE_AVG_HITTING = {
    "avg": 0.243, "obp": 0.312, "slg": 0.399, "ops": 0.711,
    "woba": 0.310, "iso": 0.156, "babip": 0.291,
    "k_pct": 0.224, "bb_pct": 0.083,
}
LEAGUE_AVG_PITCHING = {
    "era": 4.08, "whip": 1.30, "k9": 8.6, "bb9": 3.1,
    "fip": 4.08, "k_bb_pct": 0.13, "gb_pct": 1.05,
}
LEAGUE_AVG_BULLPEN_ERA = 4.20
NEUTRAL_FORM = 0.5
ROTATION_SIZE = 5  # how many of a team's starters to average for "rotation"


# ---- per-stat-line scoring (pure) ----

def _score_hitting_line(blended: dict, hit_weights: dict) -> float:
    return sum(
        normalize(blended[stat], *HIT_RANGES[stat]) * weight
        for stat, weight in hit_weights.items()
        if stat in HIT_RANGES and stat in blended
    )


def _score_pitching_line(blended: dict, pitch_weights: dict) -> float:
    return sum(
        normalize(blended[stat], *PITCH_RANGES[stat]) * weight
        for stat, weight in pitch_weights.items()
        if stat in PITCH_RANGES and stat in blended
    )


# ---- per-player scoring (does I/O via stats_service) ----

def _score_hitter(player_id: int, hit_weights: dict) -> float | None:
    blended = blend_hitting(stats_service.get_hitting_stats(player_id))
    if not blended:
        return None
    return _score_hitting_line(blended, hit_weights)


def _score_pitcher(player_id: int, pitch_weights: dict):
    """Return (score, innings) or None if no stats."""
    stats = stats_service.get_pitching_stats(player_id)
    blended = blend_pitching(stats)
    if not blended:
        return None
    innings = sum(s.get("innings", 0) for s in stats.values())
    return _score_pitching_line(blended, pitch_weights), innings


def _active_hitters(roster: list[dict]) -> list[dict]:
    return [
        p for p in roster
        if p.get("position_type") != "Pitcher" and p.get("status") == "Active"
    ]


def _pitchers(roster: list[dict]) -> list[dict]:
    return [p for p in roster if p.get("position_type") == "Pitcher"]


# ---- public API ----

def rate_team(roster: list[dict], team_id: int = 0, weights: dict | None = None) -> dict:
    """Compute a team's projected win probability vs a league-average baseline.

    Args:
        roster: list of player dicts (id, name, position_type, status).
        team_id: used for bullpen ERA and recent form (0 -> league average).
        weights: optional weights override; defaults to the saved weights.

    Returns:
        A dict with win_pct plus the component breakdown that produced it.
    """
    weights = weights or weights_store.load()
    hit_weights = weights["hit_weights"]
    pitch_weights = weights["pitch_weights"]
    balance = weights["balance"]

    off_w = balance.get("offense_weight", 0.50)
    pit_w = balance.get("pitching_weight", 0.35)
    bull_w = balance.get("bullpen_weight", 0.08)
    form_w = balance.get("recent_form_weight", 0.05)

    # Offense — average score across active hitters.
    hitter_scores = [
        s for s in (_score_hitter(p["id"], hit_weights) for p in _active_hitters(roster))
        if s is not None
    ]
    offense = sum(hitter_scores) / max(len(hitter_scores), 1)

    # Pitching — average score across the team's top rotation arms (by innings).
    pitcher_results = [
        r for r in (_score_pitcher(p["id"], pitch_weights) for p in _pitchers(roster))
        if r is not None
    ]
    pitcher_results.sort(key=lambda r: r[1], reverse=True)  # most innings first
    rotation = pitcher_results[:ROTATION_SIZE]
    pitching = sum(s for s, _ in rotation) / max(len(rotation), 1)

    # Bullpen + form from the team (league-average when team_id unknown).
    bullpen_era = stats_service.get_bullpen_era(team_id) if team_id else LEAGUE_AVG_BULLPEN_ERA
    bullpen_score = 1 - normalize(bullpen_era, 2.5, 5.5)
    form = stats_service.get_recent_form(team_id)["win_pct"] if team_id else NEUTRAL_FORM

    strength = _combine(offense, pitching, bullpen_score, form, off_w, pit_w, bull_w, form_w)
    baseline = _league_average_strength(weights)

    total = strength + baseline
    win_pct = round((strength / total) * 100, 1) if total else 50.0

    return {
        "win_pct": win_pct,
        "strength": round(strength, 4),
        "league_average_strength": round(baseline, 4),
        "components": {
            "offense": round(offense, 4),
            "pitching": round(pitching, 4),
            "bullpen_score": round(bullpen_score, 4),
            "bullpen_era": bullpen_era,
            "form": form,
            "hitters_scored": len(hitter_scores),
            "rotation_scored": len(rotation),
        },
    }


def _combine(offense, pitching, bullpen, form, off_w, pit_w, bull_w, form_w) -> float:
    # Mirror the engine's +0.5 bias on the raw pitching score so it contributes.
    pitching_adj = pitching + 0.5
    return offense * off_w + pitching_adj * pit_w + bullpen * bull_w + form * form_w


def _league_average_strength(weights: dict) -> float:
    """Strength of a perfectly league-average roster, through the same weights."""
    balance = weights["balance"]
    offense = _score_hitting_line(LEAGUE_AVG_HITTING, weights["hit_weights"])
    pitching = _score_pitching_line(LEAGUE_AVG_PITCHING, weights["pitch_weights"])
    bullpen = 1 - normalize(LEAGUE_AVG_BULLPEN_ERA, 2.5, 5.5)
    return _combine(
        offense, pitching, bullpen, NEUTRAL_FORM,
        balance.get("offense_weight", 0.50),
        balance.get("pitching_weight", 0.35),
        balance.get("bullpen_weight", 0.08),
        balance.get("recent_form_weight", 0.05),
    )
