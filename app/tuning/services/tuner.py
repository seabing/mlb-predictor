"""Random-search tuner for weight optimization.

The search samples weight perturbations around the defaults (or any seed),
evaluates each candidate against the precomputed feature set, and tracks
the best by log loss. Periodically restarts from the current best to
refine — that's the only escape from local-noise minima we bother with.
"""
from __future__ import annotations

import json
import os
import random
import time

from app.core.config import settings
from app.predictions.services.weights import (
    DEFAULT_BALANCE,
    DEFAULT_HIT_WEIGHTS,
    DEFAULT_PITCH_WEIGHTS,
)
from app.tuning.services.backtester import Backtester


class TuningHistoryLog:
    """Append-only log of recent tune runs, capped at MAX_ENTRIES newest."""

    MAX_ENTRIES = 50

    def __init__(self, path: str | None = None) -> None:
        self.path = path or settings.tuning_history_path

    def append(self, result: dict) -> None:
        history = self._load()
        history.append({
            "ts": time.time(),
            "source": result.get("source", "date_range"),
            "start_date": result.get("start_date"),
            "end_date": result.get("end_date"),
            "iterations": result.get("iterations"),
            "games": result.get("games_evaluated"),
            "baseline_metrics": result.get("baseline_metrics"),
            "best_metrics": result.get("best_metrics"),
        })
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(history[-self.MAX_ENTRIES:], f, indent=2)

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception:
            return []


class RandomSearchTuner:
    """Random search over the weight space, scored by Backtester.evaluate."""

    DEFAULT_JITTER = 0.5
    RESTART_FROM_BEST_EVERY = 20  # iterations

    def __init__(
        self,
        backtester: Backtester | None = None,
        history_log: TuningHistoryLog | None = None,
    ) -> None:
        self.backtester = backtester or Backtester()
        self.history_log = history_log or TuningHistoryLog()

    def search(
        self,
        features_list: list[dict],
        n_iter: int = 200,
        seed: int = 42,
        base_weights: dict | None = None,
    ) -> dict:
        """Run a random search; return best weights, best metrics, baseline, history."""
        random.seed(seed)
        base = base_weights or {
            "hit_weights": dict(DEFAULT_HIT_WEIGHTS),
            "pitch_weights": dict(DEFAULT_PITCH_WEIGHTS),
            "balance": dict(DEFAULT_BALANCE),
        }
        baseline = self.backtester.evaluate(features_list, base)
        best = {"weights": base, "metrics": baseline}
        history: list[dict] = [{
            "iter": 0,
            "log_loss": baseline["log_loss"],
            "accuracy": baseline["accuracy"],
            "brier": baseline["brier"],
        }]
        print(f"  baseline: log_loss={baseline['log_loss']} acc={baseline['accuracy']}")

        for i in range(1, n_iter + 1):
            # Periodically restart from the current best to refine locally
            seed_w = best["weights"] if i % self.RESTART_FROM_BEST_EVERY == 0 else base
            candidate = self._random_weights(seed_w, jitter=self.DEFAULT_JITTER)
            m = self.backtester.evaluate(features_list, candidate)
            if m["log_loss"] is not None and (
                best["metrics"]["log_loss"] is None
                or m["log_loss"] < best["metrics"]["log_loss"]
            ):
                best = {"weights": candidate, "metrics": m}
                print(f"    iter {i}: NEW BEST log_loss={m['log_loss']} acc={m['accuracy']}")
            history.append({
                "iter": i,
                "log_loss": m["log_loss"],
                "accuracy": m["accuracy"],
                "brier": m["brier"],
            })

        return {
            "best_weights": best["weights"],
            "best_metrics": best["metrics"],
            "baseline_metrics": baseline,
            "history": history,
            "iterations": n_iter,
        }

    @staticmethod
    def _random_weights(seed_weights: dict, jitter: float) -> dict:
        """Sample a perturbation of seed_weights.

        Hit/pitch weights jitter by an additive amount scaled to their
        magnitude (so big weights move more in absolute terms but stay
        proportionally similar). Balance weights stay clamped to [0, 1].
        """
        new = {"hit_weights": {}, "pitch_weights": {}, "balance": {}}
        for k, v in seed_weights["hit_weights"].items():
            new["hit_weights"][k] = round(
                v + random.uniform(-jitter, jitter) * abs(v + 0.05), 4
            )
        for k, v in seed_weights["pitch_weights"].items():
            new["pitch_weights"][k] = round(
                v + random.uniform(-jitter, jitter) * abs(v + 0.05), 4
            )
        for k, v in seed_weights["balance"].items():
            nv = v + random.uniform(-jitter, jitter) * max(v, 0.05)
            new["balance"][k] = round(max(0.0, min(1.0, nv)), 4)
        return new
