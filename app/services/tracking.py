"""Prediction tracking + grading.

Stores every prediction in SQLite (data/predictions.db). Grades pending
predictions by polling the MLB schedule endpoint for final scores.
"""
import os
import sqlite3
import json
import math
from datetime import datetime
import requests

DB_PATH = "data/predictions.db"
BASE_URL = "https://statsapi.mlb.com/api/v1"


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _conn():
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the predictions table if it doesn't exist."""
    with _conn() as c:
        c.execute("""
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
        # Index for dedupe by game_id
        c.execute("CREATE INDEX IF NOT EXISTS idx_game_id ON predictions(game_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON predictions(status)")


def log_prediction(home_team, away_team, prediction, game_id=None, game_date=None,
                   home_pitcher_id=0, away_pitcher_id=0, weights=None):
    """Insert a prediction. Replaces any existing prediction for the same game_id."""
    init_db()
    home_pct = prediction.get("home_win_pct", 50)
    away_pct = prediction.get("away_win_pct", 50)
    predicted_winner = home_team if home_pct >= away_pct else away_team
    features = {
        k: v for k, v in prediction.items()
        if k not in ("home_win_pct", "away_win_pct", "lineup_source")
    }
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        # Replace any prior prediction for the same game_id (keep one row per game)
        if game_id:
            c.execute("DELETE FROM predictions WHERE game_id = ? AND status = 'pending'", (game_id,))
        c.execute("""
            INSERT INTO predictions
            (game_id, game_date, home_team, away_team, home_pitcher_id, away_pitcher_id,
             home_win_pct, away_win_pct, predicted_winner, lineup_source,
             features_json, weights_snapshot, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            game_id, game_date, home_team, away_team, home_pitcher_id, away_pitcher_id,
            home_pct, away_pct, predicted_winner, prediction.get("lineup_source", ""),
            json.dumps(features), json.dumps(weights or {}), now
        ))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def _fetch_final(game_id):
    """Return (home_score, away_score, status) for a game_id, or None."""
    try:
        url = f"{BASE_URL}/schedule?sportId=1&gamePk={game_id}"
        resp = requests.get(url, timeout=10).json()
        for date_entry in resp.get("dates", []):
            for game in date_entry.get("games", []):
                if game["gamePk"] != game_id:
                    continue
                state = game["status"]["detailedState"]
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                if state in ("Final", "Game Over", "Completed Early"):
                    return {
                        "home_score": home.get("score"),
                        "away_score": away.get("score"),
                        "status": state,
                        "home_team_id": home["team"]["id"],
                        "away_team_id": away["team"]["id"],
                    }
                return {"status": state}
    except Exception:
        return None
    return None


def grade_pending(limit=200):
    """Look up scores for pending predictions and update their rows."""
    init_db()
    graded = 0
    skipped = 0
    with _conn() as c:
        rows = c.execute("""
            SELECT id, game_id, home_team, away_team
            FROM predictions
            WHERE status = 'pending' AND game_id IS NOT NULL
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
    for row in rows:
        info = _fetch_final(row["game_id"])
        if not info or "home_score" not in info:
            skipped += 1
            continue
        home_score = info["home_score"]
        away_score = info["away_score"]
        if home_score is None or away_score is None:
            skipped += 1
            continue
        actual = row["home_team"] if home_score > away_score else row["away_team"]
        with _conn() as c:
            pred = c.execute(
                "SELECT predicted_winner FROM predictions WHERE id = ?",
                (row["id"],)
            ).fetchone()
            correct = 1 if pred["predicted_winner"] == actual else 0
            c.execute("""
                UPDATE predictions
                SET status = 'graded',
                    actual_winner = ?,
                    home_score = ?,
                    away_score = ?,
                    correct = ?,
                    graded_at = ?
                WHERE id = ?
            """, (actual, home_score, away_score, correct,
                  datetime.utcnow().isoformat(), row["id"]))
        graded += 1
    return {"graded": graded, "skipped": skipped, "checked": len(rows)}


def list_predictions(status=None, limit=200):
    init_db()
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM predictions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def summary():
    """Running accuracy + log-loss on graded predictions."""
    init_db()
    with _conn() as c:
        rows = c.execute("""
            SELECT home_win_pct, away_win_pct, predicted_winner, actual_winner,
                   home_team, correct
            FROM predictions
            WHERE status = 'graded'
        """).fetchall()
    n = len(rows)
    if n == 0:
        return {
            "total": 0, "correct": 0, "accuracy": None,
            "log_loss": None, "brier": None, "pending": _count_pending()
        }
    correct = sum(r["correct"] or 0 for r in rows)
    total_ll = 0.0
    total_brier = 0.0
    for r in rows:
        # Probability we assigned to the team that actually won
        if r["actual_winner"] == r["home_team"]:
            p = max(min((r["home_win_pct"] or 50) / 100.0, 0.999), 0.001)
            actual = 1
        else:
            p = max(min((r["away_win_pct"] or 50) / 100.0, 0.999), 0.001)
            actual = 1
        total_ll += -math.log(p)
        # Brier on home perspective
        home_p = (r["home_win_pct"] or 50) / 100.0
        home_actual = 1 if r["actual_winner"] == r["home_team"] else 0
        total_brier += (home_p - home_actual) ** 2
    return {
        "total": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "log_loss": round(total_ll / n, 4),
        "brier": round(total_brier / n, 4),
        "pending": _count_pending(),
    }


def _count_pending():
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM predictions WHERE status = 'pending'").fetchone()[0]


def delete_prediction(pred_id):
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))
    return {"deleted": pred_id}


def reset_all():
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM predictions")
    return {"status": "reset"}
