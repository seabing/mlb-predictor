"""Confidence intervals for predictions.

Two sources of uncertainty are combined:

  1. **Statistical** — for a prediction at probability p, look up the
     historical variance of actual outcomes in that probability bucket.
     If the model has predicted 80% confidence 60 times before and was
     right 75% of the time, the standard error on that 80% claim is
     sqrt(0.75 * 0.25 / 60).

  2. **Data completeness** — penalize predictions made with partial info.
     A prediction from the day's posted lineup is more trustworthy than
     one off the depth chart with no probable pitcher posted yet.

Output is a single ± half-width in percentage points. The UI clamps the
displayed bounds to [0, 100].

The calibration buckets are cached at the singleton level and refreshed
on a TTL so per-prediction CI computation is cheap (dict lookup).
"""
from __future__ import annotations

import math
import time

from app.mlb.teams import team_id
from app.predictions import park_factors

# Minimum and maximum ± half-width (as fractions). 4% floor avoids
# misleadingly tight intervals; 25% cap avoids "67% ± 30%" silliness.
MIN_HALF_WIDTH = 0.04
MAX_HALF_WIDTH = 0.25

# Used when a bucket has too few samples to estimate variance from.
DEFAULT_BUCKET_SIZE = 20

# Calibration cache TTL — how long before we re-query the DB.
CALIBRATION_TTL_SECONDS = 600


class ConfidenceIntervalCalculator:
    def __init__(self) -> None:
        self._buckets: list[dict] = []
        self._loaded_at: float = 0.0

    # ---- calibration cache ----

    def _maybe_reload_calibration(self) -> None:
        """Refresh calibration buckets from the DB if stale."""
        now = time.time()
        if self._buckets and (now - self._loaded_at) < CALIBRATION_TTL_SECONDS:
            return
        # Lazy import to avoid a circular dep between confidence/tracking
        try:
            from app.predictions.services.tracking import prediction_store
            calib = prediction_store.calibration()
            self._buckets = calib.get("buckets", [])
            self._loaded_at = now
        except Exception:
            # Don't crash predictions if calibration fails; fall back to defaults.
            pass

    def invalidate(self) -> None:
        """Force a calibration reload on next call. Mostly for tests."""
        self._loaded_at = 0.0

    # ---- math ----

    def _historical_se(self, pick_prob: float) -> float:
        """Standard error of the actual win rate in the bucket containing pick_prob.

        Returns a fraction (e.g. 0.06 = ±6 percentage points). Uses the
        binomial standard error of the bucket's observed accuracy.
        """
        for b in self._buckets:
            if b.get("lower") is None or b.get("upper") is None:
                continue
            if b["lower"] <= pick_prob <= b["upper"]:
                n = b.get("n") or 0
                if n < 5:
                    break  # too thin — use the default below
                actual = b.get("actual_rate")
                if actual is None:
                    break
                return math.sqrt(actual * (1 - actual) / n)
        # Fallback: assume baseline binomial with a default bucket size
        return math.sqrt(pick_prob * (1 - pick_prob) / DEFAULT_BUCKET_SIZE)

    def _completeness_factor(
        self,
        lineup_source: str,
        home_team_id: int,
        away_team_id: int,
        home_pitcher_id: int,
        away_pitcher_id: int,
    ) -> float:
        """1.0 = perfect data; >1.0 multiplies the statistical width."""
        factor = 1.0
        if lineup_source == "roster":
            factor *= 1.30   # depth chart fallback, lineup not yet posted
        elif lineup_source == "manual":
            factor *= 1.10   # user-overridden, treat as semi-known
        # "mlb_api" gets no penalty (gold standard)

        if not home_pitcher_id:
            factor *= 1.15
        if not away_pitcher_id:
            factor *= 1.15

        # Slight penalty if the home park isn't in our factor table
        if home_team_id and home_team_id not in park_factors.PARK_FACTORS:
            factor *= 1.05
        # If we don't even know the home team_id (e.g. trades/test calls),
        # we can't apply park or bullpen ERA properly
        if not home_team_id:
            factor *= 1.10
        if not away_team_id:
            factor *= 1.10
        return factor

    # ---- public ----

    def half_width(
        self,
        prediction: dict,
        lineup_source: str = "roster",
        home_team_id: int = 0,
        away_team_id: int = 0,
        home_pitcher_id: int = 0,
        away_pitcher_id: int = 0,
    ) -> float:
        """Return the ± half-width as a fraction of 1.0.

        e.g. 0.08 means ±8 percentage points; for a 67% prediction the
        interval is [59%, 75%].
        """
        self._maybe_reload_calibration()
        home_pct = prediction.get("home_win_pct", 50) or 50
        away_pct = prediction.get("away_win_pct", 50) or 50
        pick_prob = max(home_pct, away_pct) / 100.0
        stat = self._historical_se(pick_prob)
        comp = self._completeness_factor(
            lineup_source, home_team_id, away_team_id,
            home_pitcher_id, away_pitcher_id,
        )
        width = stat * comp
        return max(MIN_HALF_WIDTH, min(MAX_HALF_WIDTH, width))

    def annotate(
        self,
        prediction: dict,
        *,
        lineup_source: str | None = None,
        home_team: str | None = None,
        away_team: str | None = None,
        home_pitcher_id: int | None = None,
        away_pitcher_id: int | None = None,
    ) -> dict:
        """Mutates `prediction` to add confidence_interval (in percentage
        points) plus low/high bounds for both sides. Idempotent."""
        ls = lineup_source or prediction.get("lineup_source", "roster")
        hpi = home_pitcher_id if home_pitcher_id is not None else prediction.get("home_pitcher_id", 0) or 0
        api_ = away_pitcher_id if away_pitcher_id is not None else prediction.get("away_pitcher_id", 0) or 0
        h_code = home_team or prediction.get("home_team", "")
        a_code = away_team or prediction.get("away_team", "")
        hti = team_id(h_code) or 0
        ati = team_id(a_code) or 0

        hw = self.half_width(
            prediction,
            lineup_source=ls,
            home_team_id=hti,
            away_team_id=ati,
            home_pitcher_id=hpi,
            away_pitcher_id=api_,
        )
        ci_pts = round(hw * 100, 1)
        home_p = prediction.get("home_win_pct", 50) or 50
        away_p = prediction.get("away_win_pct", 50) or 50
        prediction["confidence_interval"] = ci_pts
        prediction["home_win_pct_low"] = round(max(0.0, home_p - ci_pts), 1)
        prediction["home_win_pct_high"] = round(min(100.0, home_p + ci_pts), 1)
        prediction["away_win_pct_low"] = round(max(0.0, away_p - ci_pts), 1)
        prediction["away_win_pct_high"] = round(min(100.0, away_p + ci_pts), 1)
        return prediction


confidence_calculator = ConfidenceIntervalCalculator()
