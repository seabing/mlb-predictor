"""Prediction engine.

Combines lineup hitting, starter pitching, bullpen ERA, recent form, and
park factor into a single win-probability per team. Weights are pulled
from WeightsStore; stats from StatsService.

The same engine is reused by:
  - /api/predict and /api/predict/today (user-facing predictions)
  - the auto-predict scheduler
  - the backfill tool
  - (indirectly) the tuner, which precomputes features and re-scores via
    its own evaluator
"""
from __future__ import annotations

from app.mlb.stats import StatsService, blend_hitting, blend_pitching, stats_service
from app.predictions import park_factors
from app.predictions.services.weights import (
    HIT_RANGES,
    PITCH_RANGES,
    WeightsStore,
    normalize,
    weights_store,
)

LEAGUE_AVG_BULLPEN_ERA = 4.20
NEUTRAL_FORM = 0.5


class PredictionEngine:
    """Run one prediction. Stateless except for its weights + stats deps."""

    def __init__(
        self,
        weights_store: WeightsStore | None = None,
        stats: StatsService | None = None,
    ) -> None:
        self.weights_store = weights_store or globals()["weights_store"]
        self.stats = stats or stats_service

    # ---- public ----

    def predict_game(
        self,
        home_roster: list[dict],
        away_roster: list[dict],
        home_pitcher_id: int,
        away_pitcher_id: int,
        home_team_id: int = 0,
        away_team_id: int = 0,
    ) -> dict:
        weights = self.weights_store.load()
        balance = weights["balance"]

        home_hit = self._score_lineup(home_roster, away_pitcher_id, weights, split="home")
        away_hit = self._score_lineup(away_roster, home_pitcher_id, weights, split="away")
        home_pitch = self._score_pitcher(home_pitcher_id, weights)
        away_pitch = self._score_pitcher(away_pitcher_id, weights)

        home_bullpen_era = (
            self.stats.get_bullpen_era(home_team_id) if home_team_id else LEAGUE_AVG_BULLPEN_ERA
        )
        away_bullpen_era = (
            self.stats.get_bullpen_era(away_team_id) if away_team_id else LEAGUE_AVG_BULLPEN_ERA
        )
        home_bullpen_score = 1 - normalize(home_bullpen_era, 2.5, 5.5)
        away_bullpen_score = 1 - normalize(away_bullpen_era, 2.5, 5.5)

        home_form = (
            self.stats.get_recent_form(home_team_id)["win_pct"] if home_team_id else NEUTRAL_FORM
        )
        away_form = (
            self.stats.get_recent_form(away_team_id)["win_pct"] if away_team_id else NEUTRAL_FORM
        )

        home_park = park_factors.get(home_team_id)

        # Bias raw pitcher score toward positive so it adds to score totals
        home_pitch_adj = home_pitch + 0.5
        away_pitch_adj = away_pitch + 0.5

        off_w = balance.get("offense_weight", 0.50)
        pit_w = balance.get("pitching_weight", 0.35)
        bull_w = balance.get("bullpen_weight", 0.08)
        form_w = balance.get("recent_form_weight", 0.05)
        pf_w = balance.get("park_factor_weight", 0.05)

        home_score = (
            home_hit * off_w
            + home_pitch_adj * pit_w
            + home_bullpen_score * bull_w
            + home_form * form_w
        )
        away_score = (
            away_hit * off_w
            + away_pitch_adj * pit_w
            + away_bullpen_score * bull_w
            + away_form * form_w
        )

        # Park factor — boosts home, depresses away (multiplicatively).
        home_score *= 1 + (home_park - 1) * pf_w * 10
        away_score *= 1 - (home_park - 1) * pf_w * 5

        total = home_score + away_score
        if total == 0:
            return {"home_win_pct": 50, "away_win_pct": 50}

        home_win_pct = round((home_score / total) * 100, 1)
        away_win_pct = round(100 - home_win_pct, 1)

        return {
            "home_win_pct": home_win_pct,
            "away_win_pct": away_win_pct,
            "home_offense_score": round(home_hit, 4),
            "away_offense_score": round(away_hit, 4),
            "home_pitcher_score": round(home_pitch, 4),
            "away_pitcher_score": round(away_pitch, 4),
            "home_bullpen_era": home_bullpen_era,
            "away_bullpen_era": away_bullpen_era,
            "home_form": home_form,
            "away_form": away_form,
            "park_factor": home_park,
        }

    # ---- internal scoring ----

    def _score_lineup(self, roster, pitcher_id, weights, split):
        hit_weights = weights["hit_weights"]
        bvp_w = weights["balance"].get("bvp_weight", 0.15)
        hitters = [
            p for p in roster
            if p.get("position_type") != "Pitcher" and p.get("status") == "Active"
        ]
        total_score = 0.0
        scored = 0
        for player in hitters:
            split_stats = self.stats.get_hitting_splits(player["id"], split)
            stats = split_stats if split_stats else self.stats.get_hitting_stats(player["id"])
            blended = blend_hitting(stats)
            if not blended:
                continue

            score = sum(
                normalize(blended[stat], *HIT_RANGES[stat]) * weight
                for stat, weight in hit_weights.items()
                if stat in HIT_RANGES
            )

            # Batter-vs-pitcher history bonus (if sample is large enough)
            if pitcher_id:
                bvp = self.stats.get_batter_vs_pitcher(player["id"], pitcher_id)
                if bvp:
                    bvp_score = (
                        normalize(bvp["obp"], 0.250, 0.500) * 0.5
                        + normalize(bvp["slg"], 0.300, 0.700) * 0.5
                    )
                    score = score * (1 - bvp_w) + bvp_score * bvp_w

            total_score += score
            scored += 1
        return total_score / max(scored, 1)

    def _score_pitcher(self, player_id, weights):
        if not player_id:
            return 0.0
        pitch_weights = weights["pitch_weights"]
        stats = self.stats.get_pitching_stats(player_id)
        blended = blend_pitching(stats)
        if not blended:
            return 0.0
        return sum(
            normalize(blended[stat], *PITCH_RANGES[stat]) * weight
            for stat, weight in pitch_weights.items()
            if stat in PITCH_RANGES
        )


# Module-level singleton — most callers use this.
prediction_engine = PredictionEngine()
