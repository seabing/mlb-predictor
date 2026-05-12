"""Backtester — replays historical games through the prediction model.

Three responsibilities:

  1. Hydrate the data: fetch Final games for a date range, then their
     boxscore lineups, then per-player stat features.
  2. Cache aggressively: every hop hits `FeatureCache` first.
  3. Score: given features + weights, compute P(home wins) and aggregate
     log loss / accuracy / Brier across a list of games.

The scoring math here is *parameterized* over weights so the tuner can
evaluate thousands of weight combos without re-fetching. That's why this
class re-implements the scoring formula instead of calling PredictionEngine.
The two are kept in sync by hand; if you change one, change both.
"""
from __future__ import annotations

import math
from typing import Iterable

import requests

from app.mlb.client import MLBStatsClient, client as mlb_client_singleton
from app.mlb.stats import StatsService, blend_hitting, blend_pitching, stats_service
from app.predictions import park_factors
from app.predictions.services.weights import HIT_RANGES, PITCH_RANGES, normalize
from app.tuning.services.feature_cache import FeatureCache


class Backtester:
    BASE_URL = "https://statsapi.mlb.com/api/v1"
    BULLPEN_NORMALIZE_RANGE = (2.5, 5.5)
    BVP_OBP_RANGE = (0.250, 0.500)
    BVP_SLG_RANGE = (0.300, 0.700)
    FINAL_STATES = ("Final", "Game Over", "Completed Early")

    def __init__(
        self,
        cache: FeatureCache | None = None,
        mlb_client: MLBStatsClient | None = None,
        stats: StatsService | None = None,
    ) -> None:
        self.cache = cache or FeatureCache()
        self.mlb = mlb_client or mlb_client_singleton
        self.stats = stats or stats_service

    # =====================================================================
    # Game discovery
    # =====================================================================

    def fetch_finals(self, start_date: str, end_date: str) -> list[dict]:
        """Return all Final games in [start_date, end_date]. Cached per range."""
        key = f"finals::{start_date}::{end_date}"
        if cached := self.cache.get(key):
            return cached
        resp = requests.get(
            f"{self.BASE_URL}/schedule",
            params={
                "sportId": 1,
                "startDate": start_date,
                "endDate": end_date,
                "gameType": "R",
            },
            timeout=30,
        ).json()
        games: list[dict] = []
        for date_entry in resp.get("dates", []):
            for game in date_entry.get("games", []):
                if game["status"]["detailedState"] not in self.FINAL_STATES:
                    continue
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                hs = home.get("score")
                as_ = away.get("score")
                if hs is None or as_ is None:
                    continue
                games.append({
                    "game_id": game["gamePk"],
                    "date": date_entry["date"],
                    "home_team_id": home["team"]["id"],
                    "away_team_id": away["team"]["id"],
                    "home_score": hs,
                    "away_score": as_,
                })
        self.cache.set(key, games)
        self.cache.save()
        return games

    def fetch_lineups_for(self, games: Iterable[dict]) -> list[dict]:
        """Add starter ids + batting-order ids to each game (from the boxscore)."""
        enriched: list[dict] = []
        for g in games:
            key = f"box::{g['game_id']}"
            box = self.cache.get(key)
            if box is None:
                box = self.mlb.get_lineup(g["game_id"])
                self.cache.set(key, box)
            home = box.get("home", {})
            away = box.get("away", {})
            if not home.get("lineup_available") or not away.get("lineup_available"):
                continue
            if not home.get("starter_id") or not away.get("starter_id"):
                continue
            g2 = dict(g)
            g2["home_starter_id"] = home["starter_id"]
            g2["away_starter_id"] = away["starter_id"]
            g2["home_lineup_ids"] = [p["id"] for p in home["lineup"]]
            g2["away_lineup_ids"] = [p["id"] for p in away["lineup"]]
            enriched.append(g2)
        self.cache.save()
        return enriched

    # =====================================================================
    # Feature precomputation
    # =====================================================================

    def precompute_features(self, games: list[dict]) -> list[dict]:
        """For each game, build a self-contained features dict ready for scoring.

        Heavy I/O happens here (per-player stat fetches), so progress is
        logged so the user can see something happen during a slow first run.
        """
        print(f"  Precomputing features for {len(games)} games...")
        out: list[dict] = []
        for i, g in enumerate(games, 1):
            if i % 25 == 0:
                print(f"    {i}/{len(games)}...")
            try:
                features = {
                    "game_id": g["game_id"],
                    "date": g["date"],
                    "home_team_id": g["home_team_id"],
                    "away_team_id": g["away_team_id"],
                    "home_won": 1 if g["home_score"] > g["away_score"] else 0,
                    "home_lineup": self._lineup_features(
                        g["home_lineup_ids"], g["away_starter_id"], split="home",
                    ),
                    "away_lineup": self._lineup_features(
                        g["away_lineup_ids"], g["home_starter_id"], split="away",
                    ),
                    "home_pitch": self._pitcher_normalized(g["home_starter_id"]) or {},
                    "away_pitch": self._pitcher_normalized(g["away_starter_id"]) or {},
                    "home_bullpen_era": self._team_bullpen(g["home_team_id"]),
                    "away_bullpen_era": self._team_bullpen(g["away_team_id"]),
                    "park_factor": park_factors.get(g["home_team_id"]),
                }
                out.append(features)
            except Exception as e:
                print(f"    skip game {g['game_id']}: {e}")
        self.cache.save()
        return out

    def _lineup_features(self, player_ids, opposing_pitcher_id, split: str) -> list[dict]:
        players: list[dict] = []
        for pid in player_ids:
            feats = self._hitter_normalized(pid, split)
            if feats is None:
                continue
            feats["_bvp"] = self._bvp(pid, opposing_pitcher_id)
            players.append(feats)
        return players

    def _hitter_normalized(self, player_id: int, split: str) -> dict | None:
        key = f"hit::{player_id}::{split}"
        if self.cache.has(key):
            return self.cache.get(key)
        splits = self.stats.get_hitting_splits(player_id, split)
        stats = splits if splits else self.stats.get_hitting_stats(player_id)
        blended = blend_hitting(stats)
        if not blended:
            self.cache.set(key, None)
            return None
        norm = {
            stat: normalize(blended.get(stat, 0), low, high)
            for stat, (low, high) in HIT_RANGES.items()
        }
        self.cache.set(key, norm)
        return norm

    def _pitcher_normalized(self, pitcher_id: int) -> dict | None:
        key = f"pitch::{pitcher_id}"
        if self.cache.has(key):
            return self.cache.get(key)
        stats = self.stats.get_pitching_stats(pitcher_id)
        blended = blend_pitching(stats)
        if not blended:
            self.cache.set(key, None)
            return None
        norm = {
            stat: normalize(blended.get(stat, 0), low, high)
            for stat, (low, high) in PITCH_RANGES.items()
        }
        self.cache.set(key, norm)
        return norm

    def _bvp(self, batter_id: int, pitcher_id: int) -> dict | None:
        key = f"bvp::{batter_id}::{pitcher_id}"
        if self.cache.has(key):
            return self.cache.get(key)
        bvp = self.stats.get_batter_vs_pitcher(batter_id, pitcher_id)
        if not bvp:
            self.cache.set(key, None)
            return None
        normalized = {
            "obp": normalize(bvp["obp"], *self.BVP_OBP_RANGE),
            "slg": normalize(bvp["slg"], *self.BVP_SLG_RANGE),
        }
        self.cache.set(key, normalized)
        return normalized

    def _team_bullpen(self, team_id: int) -> float:
        key = f"bullpen::{team_id}"
        if self.cache.has(key):
            return self.cache.get(key)
        era = self.stats.get_bullpen_era(team_id)
        self.cache.set(key, era)
        return era

    # =====================================================================
    # Scoring + evaluation
    # =====================================================================

    def score_one(self, features: dict, weights: dict) -> float:
        """Compute P(home wins) for one game's features given a weights dict."""
        hit_w = weights["hit_weights"]
        pit_w = weights["pitch_weights"]
        bal = weights["balance"]
        bvp_w = bal.get("bvp_weight", 0.15)
        pf_w = bal.get("park_factor_weight", 0.05)

        def lineup_score(players):
            if not players:
                return 0.0
            total = 0.0
            for p in players:
                score = sum(p.get(stat, 0) * w for stat, w in hit_w.items())
                bvp = p.get("_bvp")
                if bvp is not None:
                    bvp_score = bvp["obp"] * 0.5 + bvp["slg"] * 0.5
                    score = score * (1 - bvp_w) + bvp_score * bvp_w
                total += score
            return total / len(players)

        def pitcher_score(p):
            if not p:
                return 0.0
            return sum(p.get(stat, 0) * w for stat, w in pit_w.items())

        home_hit = lineup_score(features["home_lineup"])
        away_hit = lineup_score(features["away_lineup"])
        home_pitch = pitcher_score(features["home_pitch"])
        away_pitch = pitcher_score(features["away_pitch"])
        home_bull = 1 - normalize(features["home_bullpen_era"], *self.BULLPEN_NORMALIZE_RANGE)
        away_bull = 1 - normalize(features["away_bullpen_era"], *self.BULLPEN_NORMALIZE_RANGE)

        off_w = bal.get("offense_weight", 0.50)
        pit_balance = bal.get("pitching_weight", 0.35)
        bull_w = bal.get("bullpen_weight", 0.08)
        form_w = bal.get("recent_form_weight", 0.05)

        home_score = (
            home_hit * off_w + (home_pitch + 0.5) * pit_balance
            + home_bull * bull_w + 0.5 * form_w
        )
        away_score = (
            away_hit * off_w + (away_pitch + 0.5) * pit_balance
            + away_bull * bull_w + 0.5 * form_w
        )
        pf = features["park_factor"]
        home_score *= (1 + (pf - 1) * pf_w * 10)
        away_score *= (1 - (pf - 1) * pf_w * 5)
        total = home_score + away_score
        if total == 0:
            return 0.5
        return home_score / total

    def evaluate(self, features_list: list[dict], weights: dict) -> dict:
        """Compute log loss, accuracy, Brier across a list of games."""
        if not features_list:
            return {"log_loss": None, "accuracy": None, "brier": None, "n": 0}
        ll = 0.0
        bs = 0.0
        correct = 0
        for f in features_list:
            p = self.score_one(f, weights)
            p = max(min(p, 0.999), 0.001)
            actual = f["home_won"]
            ll += -(actual * math.log(p) + (1 - actual) * math.log(1 - p))
            bs += (p - actual) ** 2
            if (p >= 0.5) == (actual == 1):
                correct += 1
        n = len(features_list)
        return {
            "log_loss": round(ll / n, 4),
            "accuracy": round(correct / n, 4),
            "brier": round(bs / n, 4),
            "n": n,
        }
