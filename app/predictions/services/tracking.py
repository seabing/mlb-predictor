"""Prediction tracking + grading.

`PredictionStore` owns the predictions table: schema, idempotent inserts
(one row per game_id, first wins), grading against MLB final scores,
summary stats, and dedupe of any pre-fix duplicates.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from sqlite3 import Connection

import requests

from app.core.config import settings
from app.core.db import SqliteStore
from app.predictions.models import Summary


class PredictionStore(SqliteStore):
    MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
    FINAL_STATES = ("Final", "Game Over", "Completed Early")

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__(db_path or settings.predictions_db_path)

    def _schema(self, conn: Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER,
                game_date TEXT,
                home_team TEXT,
                away_team TEXT,
                home_pitcher_id INTEGER,
                away_pitcher_id INTEGER,
                home_win_pct REAL,
                away_win_pct REAL,
                predicted_winner TEXT,
                lineup_source TEXT,
                features_json TEXT,
                weights_snapshot TEXT,
                status TEXT DEFAULT 'pending',
                actual_winner TEXT,
                home_score INTEGER,
                away_score INTEGER,
                correct INTEGER,
                created_at TEXT,
                graded_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_id ON predictions(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON predictions(status)")

    # ---- write ----

    def log(
        self,
        home_team: str,
        away_team: str,
        prediction: dict,
        game_id: int | None = None,
        game_date: str | None = None,
        home_pitcher_id: int = 0,
        away_pitcher_id: int = 0,
        weights: dict | None = None,
        force_replace: bool = False,
    ) -> int:
        """Idempotent insert. Returns the row id of whatever ends up canonical
        for this game_id. See docstring on the legacy function for the rules."""
        self.init()
        home_pct = prediction.get("home_win_pct", 50)
        away_pct = prediction.get("away_win_pct", 50)
        predicted_winner = home_team if home_pct >= away_pct else away_team
        features = {
            k: v for k, v in prediction.items()
            if k not in ("home_win_pct", "away_win_pct", "lineup_source")
        }
        now = datetime.utcnow().isoformat()

        with self.connect() as c:
            if game_id:
                existing = c.execute(
                    "SELECT id, status FROM predictions WHERE game_id = ? "
                    "ORDER BY (status='graded') DESC, id ASC",
                    (game_id,),
                ).fetchall()
                if existing and not force_replace:
                    # Keep the best existing row (graded preferred, then oldest);
                    # drop any stragglers.
                    keeper_id = existing[0]["id"]
                    for r in existing[1:]:
                        c.execute("DELETE FROM predictions WHERE id = ?", (r["id"],))
                    return keeper_id
                if existing and force_replace:
                    # Never overwrite a graded row.
                    graded = [r for r in existing if r["status"] == "graded"]
                    if graded:
                        keeper_id = graded[0]["id"]
                        for r in existing:
                            if r["id"] != keeper_id:
                                c.execute("DELETE FROM predictions WHERE id = ?", (r["id"],))
                        return keeper_id
                    c.execute("DELETE FROM predictions WHERE game_id = ?", (game_id,))
            c.execute(
                """
                INSERT INTO predictions
                (game_id, game_date, home_team, away_team, home_pitcher_id, away_pitcher_id,
                 home_win_pct, away_win_pct, predicted_winner, lineup_source,
                 features_json, weights_snapshot, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    game_id, game_date, home_team, away_team,
                    home_pitcher_id, away_pitcher_id,
                    home_pct, away_pct, predicted_winner,
                    prediction.get("lineup_source", ""),
                    json.dumps(features), json.dumps(weights or {}), now,
                ),
            )
            return c.execute("SELECT last_insert_rowid()").fetchone()[0]

    # ---- read ----

    def get_by_game_id(self, game_id: int) -> dict | None:
        self.init()
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM predictions WHERE game_id = ? "
                "ORDER BY (status='graded') DESC, id ASC LIMIT 1",
                (game_id,),
            ).fetchone()
        return dict(row) if row else None

    def list(
        self,
        status: str | None = None,
        limit: int = 200,
        game_date: str | None = None,
    ) -> list[dict]:
        self.init()
        sql = "SELECT * FROM predictions WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if game_date:
            sql += " AND game_date = ?"
            params.append(game_date)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def available_dates(self, limit: int = 90) -> list[dict]:
        self.init()
        with self.connect() as c:
            rows = c.execute(
                "SELECT game_date, COUNT(*) AS n, "
                "SUM(CASE WHEN status='graded' THEN 1 ELSE 0 END) AS graded, "
                "SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) AS correct "
                "FROM predictions WHERE game_date IS NOT NULL "
                "GROUP BY game_date ORDER BY game_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict:
        self.init()
        self.dedupe()  # self-heal before reading totals
        with self.connect() as c:
            rows = c.execute(
                "SELECT home_win_pct, away_win_pct, predicted_winner, "
                "actual_winner, home_team, correct "
                "FROM predictions WHERE status = 'graded'"
            ).fetchall()
            pending = c.execute(
                "SELECT COUNT(*) FROM predictions WHERE status = 'pending'"
            ).fetchone()[0]
        n = len(rows)
        if n == 0:
            return Summary(
                total=0, correct=0, accuracy=None,
                log_loss=None, brier=None, pending=pending,
            ).to_dict()
        correct = sum(r["correct"] or 0 for r in rows)
        total_ll = 0.0
        total_brier = 0.0
        for r in rows:
            if r["actual_winner"] == r["home_team"]:
                p = max(min((r["home_win_pct"] or 50) / 100.0, 0.999), 0.001)
            else:
                p = max(min((r["away_win_pct"] or 50) / 100.0, 0.999), 0.001)
            total_ll += -math.log(p)
            home_p = (r["home_win_pct"] or 50) / 100.0
            home_actual = 1 if r["actual_winner"] == r["home_team"] else 0
            total_brier += (home_p - home_actual) ** 2
        return Summary(
            total=n,
            correct=correct,
            accuracy=round(correct / n, 4),
            log_loss=round(total_ll / n, 4),
            brier=round(total_brier / n, 4),
            pending=pending,
        ).to_dict()

    # ---- grading ----

    def grade_pending(self, limit: int = 200) -> dict:
        self.init()
        with self.connect() as c:
            rows = c.execute(
                "SELECT id, game_id, home_team, away_team FROM predictions "
                "WHERE status = 'pending' AND game_id IS NOT NULL "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()

        graded = 0
        skipped = 0
        now = datetime.utcnow().isoformat()
        for row in rows:
            info = self._fetch_final(row["game_id"])
            if not info or "home_score" not in info:
                skipped += 1
                continue
            home_score = info["home_score"]
            away_score = info["away_score"]
            if home_score is None or away_score is None:
                skipped += 1
                continue
            actual = row["home_team"] if home_score > away_score else row["away_team"]
            with self.connect() as c:
                pred = c.execute(
                    "SELECT predicted_winner FROM predictions WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                correct = 1 if pred["predicted_winner"] == actual else 0
                c.execute(
                    "UPDATE predictions SET status='graded', actual_winner=?, "
                    "home_score=?, away_score=?, correct=?, graded_at=? WHERE id=?",
                    (actual, home_score, away_score, correct, now, row["id"]),
                )
            graded += 1
        return {"graded": graded, "skipped": skipped, "checked": len(rows)}

    def _fetch_final(self, game_id: int) -> dict | None:
        try:
            resp = requests.get(
                self.MLB_SCHEDULE_URL,
                params={"sportId": 1, "gamePk": game_id},
                timeout=10,
            ).json()
        except Exception:
            return None
        for date_entry in resp.get("dates", []):
            for game in date_entry.get("games", []):
                if game["gamePk"] != game_id:
                    continue
                state = game["status"]["detailedState"]
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                if state in self.FINAL_STATES:
                    return {
                        "home_score": home.get("score"),
                        "away_score": away.get("score"),
                        "status": state,
                        "home_team_id": home["team"]["id"],
                        "away_team_id": away["team"]["id"],
                    }
                return {"status": state}
        return None

    # ---- cleanup ----

    def dedupe(self) -> dict:
        """Collapse any duplicate rows per game_id. Keep graded over pending,
        then oldest. Returns {deleted, duplicate_games_found}."""
        self.init()
        deleted = 0
        with self.connect() as c:
            dupe_ids = [
                r["game_id"] for r in c.execute(
                    "SELECT game_id FROM predictions WHERE game_id IS NOT NULL "
                    "GROUP BY game_id HAVING COUNT(*) > 1"
                ).fetchall()
            ]
            for gid in dupe_ids:
                rows = c.execute(
                    "SELECT id FROM predictions WHERE game_id = ? "
                    "ORDER BY (status = 'graded') DESC, created_at DESC",
                    (gid,),
                ).fetchall()
                for r in rows[1:]:
                    c.execute("DELETE FROM predictions WHERE id = ?", (r["id"],))
                    deleted += 1
        return {"deleted": deleted, "duplicate_games_found": len(dupe_ids)}

    def delete(self, pred_id: int) -> dict:
        self.init()
        with self.connect() as c:
            c.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))
        return {"deleted": pred_id}

    def reset_all(self) -> dict:
        self.init()
        with self.connect() as c:
            c.execute("DELETE FROM predictions")
        return {"status": "reset"}


# Module-level singleton
prediction_store = PredictionStore()
