"""Typed shapes for the predictions feature.

Keep these thin — they exist to make the data contract obvious at the call
site and to enable type-aware editing. Conversion helpers live on the
dataclasses themselves (`Weights.to_dict()` etc).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Weights:
    """Tunable model coefficients. Loaded from data/weights.json."""

    hit_weights: dict[str, float] = field(default_factory=dict)
    pitch_weights: dict[str, float] = field(default_factory=dict)
    balance: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> "Weights":
        return cls(
            hit_weights=dict(raw.get("hit_weights", {})),
            pitch_weights=dict(raw.get("pitch_weights", {})),
            balance=dict(raw.get("balance", {})),
        )

    def to_dict(self) -> dict:
        return {
            "hit_weights": self.hit_weights,
            "pitch_weights": self.pitch_weights,
            "balance": self.balance,
        }


@dataclass
class PredictionResult:
    """What `PredictionEngine.predict()` returns. Includes both probabilities
    and the component scores so the UI can show breakdowns."""

    home_win_pct: float
    away_win_pct: float
    home_offense_score: float = 0.0
    away_offense_score: float = 0.0
    home_pitcher_score: float = 0.0
    away_pitcher_score: float = 0.0
    home_bullpen_era: float = 0.0
    away_bullpen_era: float = 0.0
    home_form: float = 0.5
    away_form: float = 0.5
    park_factor: float = 1.0
    lineup_source: str = ""
    prediction_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Summary:
    """Aggregate stats over graded predictions."""

    total: int
    correct: int
    accuracy: float | None
    log_loss: float | None
    brier: float | None
    pending: int

    def to_dict(self) -> dict:
        return asdict(self)
