"""Visitor tracking — email-keyed, summary-only.

A visitor is identified by email (lowercased). Each visitor has a stable
visitor_id (UUID4) stored in their cookie. Two tables:

  - visitors  — one row per email (canonical)
  - visit_log — minimal touch records, capped at LOG_CAP newest rows
"""
from __future__ import annotations

import uuid
from datetime import datetime
from sqlite3 import Connection

from app.core.config import settings
from app.core.db import SqliteStore


class VisitorStore(SqliteStore):
    LOG_CAP = 5000

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__(db_path or settings.visitors_db_path)

    def _schema(self, conn: Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visitors (
                visitor_id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                visit_count INTEGER NOT NULL DEFAULT 1,
                last_path TEXT,
                last_user_agent TEXT,
                last_ip TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                visitor_id TEXT,
                ts TEXT NOT NULL,
                path TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visitors_email ON visitors(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_log_ts ON visit_log(ts)")

    # ---- public ----

    def register(self, email: str, user_agent: str | None = None,
                 ip: str | None = None) -> str:
        """Look up or create a visitor row for this email. Returns visitor_id.
        Idempotent: returns the existing visitor_id if the email is known."""
        self.init()
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValueError("invalid email")
        now = datetime.utcnow().isoformat()
        with self.connect() as c:
            row = c.execute(
                "SELECT visitor_id FROM visitors WHERE email = ?", (email,)
            ).fetchone()
            if row:
                visitor_id = row["visitor_id"]
                c.execute(
                    "UPDATE visitors SET last_seen = ?, "
                    "visit_count = visit_count + 1, "
                    "last_user_agent = COALESCE(?, last_user_agent), "
                    "last_ip = COALESCE(?, last_ip) "
                    "WHERE visitor_id = ?",
                    (now, user_agent, ip, visitor_id),
                )
                return visitor_id
            visitor_id = str(uuid.uuid4())
            c.execute(
                "INSERT INTO visitors (visitor_id, email, first_seen, last_seen, "
                "visit_count, last_user_agent, last_ip) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (visitor_id, email, now, now, user_agent, ip),
            )
            return visitor_id

    def touch(self, visitor_id: str | None, path: str | None = None,
              user_agent: str | None = None, ip: str | None = None) -> None:
        """Record a hit from a known visitor. Updates last_seen/count and adds
        a capped log entry. Safe to call on every request."""
        if not visitor_id:
            return
        self.init()
        now = datetime.utcnow().isoformat()
        with self.connect() as c:
            existing = c.execute(
                "SELECT visitor_id FROM visitors WHERE visitor_id = ?",
                (visitor_id,),
            ).fetchone()
            if not existing:
                return  # cookie points at unknown visitor — ignore
            c.execute(
                "UPDATE visitors SET last_seen = ?, "
                "visit_count = visit_count + 1, "
                "last_path = ?, "
                "last_user_agent = COALESCE(?, last_user_agent), "
                "last_ip = COALESCE(?, last_ip) "
                "WHERE visitor_id = ?",
                (now, path, user_agent, ip, visitor_id),
            )
            c.execute(
                "INSERT INTO visit_log (visitor_id, ts, path) VALUES (?, ?, ?)",
                (visitor_id, now, path),
            )
            # Trim log to LOG_CAP newest entries
            c.execute(
                "DELETE FROM visit_log WHERE id NOT IN "
                "(SELECT id FROM visit_log ORDER BY id DESC LIMIT ?)",
                (self.LOG_CAP,),
            )

    def summary(self) -> dict:
        """Aggregate stats for the admin dashboard."""
        self.init()
        with self.connect() as c:
            rows = c.execute("""
                SELECT email, visitor_id, first_seen, last_seen, visit_count,
                       last_path, last_user_agent, last_ip
                FROM visitors ORDER BY last_seen DESC
            """).fetchall()
            total_hits = c.execute(
                "SELECT SUM(visit_count) FROM visitors"
            ).fetchone()[0] or 0
        return {
            "unique_visitors": len(rows),
            "total_hits": total_hits,
            "visitors": [dict(r) for r in rows],
        }


visitor_store = VisitorStore()
