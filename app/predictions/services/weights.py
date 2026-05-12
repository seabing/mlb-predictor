"""Weights persistence — load from disk, fall back to defaults, save back.

DEFAULT_* constants are exported for callers that want the raw dicts (the
tuner uses them as the random-search seed). Everyone else should use
WeightsStore which owns the file path and handles the JSON I/O.
"""
from __future__ import annotations

import json
import os

from app.core.config import settings
from app.predictions.models import Weights

DEFAULT_HIT_WEIGHTS: dict[str, float] = {
    "obp": 0.25,
    "slg": 0.20,
    "woba": 0.20,
    "avg": 0.10,
    "iso": 0.10,
    "bb_pct": 0.08,
    "k_pct": -0.07,
    "babip": 0.02,
}

DEFAULT_PITCH_WEIGHTS: dict[str, float] = {
    "fip": -0.30,
    "era": -0.20,
    "whip": -0.20,
    "k9": 0.15,
    "k_bb_pct": 0.10,
    "bb9": -0.05,
    "gb_pct": 0.00,
}

DEFAULT_BALANCE: dict[str, float] = {
    "offense_weight": 0.50,
    "pitching_weight": 0.35,
    "bullpen_weight": 0.08,
    "recent_form_weight": 0.05,
    "bvp_weight": 0.15,
    "park_factor_weight": 0.05,
}

# Min/max ranges used to normalize raw stats into [0, 1].
HIT_RANGES: dict[str, tuple[float, float]] = {
    "obp": (0.280, 0.420),
    "slg": (0.350, 0.550),
    "avg": (0.220, 0.320),
    "woba": (0.270, 0.420),
    "iso": (0.080, 0.280),
    "babip": (0.250, 0.380),
    "bb_pct": (0.04, 0.16),
    "k_pct": (0.10, 0.35),
}

PITCH_RANGES: dict[str, tuple[float, float]] = {
    "era": (2.0, 6.0),
    "whip": (0.90, 1.60),
    "k9": (5.0, 13.0),
    "bb9": (1.5, 5.0),
    "fip": (2.5, 5.5),
    "k_bb_pct": (-0.05, 0.25),
    "gb_pct": (0.5, 3.0),
}


def normalize(value: float, low: float, high: float) -> float:
    """Min-max clamp into [0, 1]. Used everywhere stats get scored."""
    if high == low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def default_weights_dict() -> dict:
    return {
        "hit_weights": dict(DEFAULT_HIT_WEIGHTS),
        "pitch_weights": dict(DEFAULT_PITCH_WEIGHTS),
        "balance": dict(DEFAULT_BALANCE),
    }


class WeightsStore:
    """Persists Weights to a JSON file. Idempotent loads, atomic-ish saves."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or settings.weights_path

    def load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[weights] load failed, using defaults: {e}")
        return default_weights_dict()

    def save(self, weights: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(weights, f, indent=2)

    def reset(self) -> dict:
        defaults = default_weights_dict()
        self.save(defaults)
        return defaults

    # ---- typed accessors ----

    def load_typed(self) -> Weights:
        return Weights.from_dict(self.load())

    def save_typed(self, weights: Weights) -> None:
        self.save(weights.to_dict())


# Module-level singleton
weights_store = WeightsStore()
