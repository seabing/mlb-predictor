"""Trade simulator store.

JSON-backed list of fake trades the user has made in the Trade Simulator
page. Small, simple, no concurrent writes to worry about.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from app.core.config import settings


@dataclass
class Trade:
    player_id: int
    player_name: str
    from_team: str
    to_team: str


class TradesStore:
    """Append-only list of trades, persisted to JSON."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(settings.data_dir, "trades.json")

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, trades: list[dict]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(trades, f, indent=2)

    def list(self) -> dict:
        return {"trades": self._load()}

    def add(self, payload: dict) -> dict:
        trades = self._load()
        trades.append(asdict(Trade(
            player_id=payload["player_id"],
            player_name=payload["player_name"],
            from_team=payload["from_team"],
            to_team=payload["to_team"],
        )))
        self._save(trades)
        return {"status": "ok", "trades": trades}

    def reset(self) -> dict:
        self._save([])
        return {"status": "reset"}


trades_store = TradesStore()
