"""SQLite store base class.

Each feature that needs a SQLite DB subclasses SqliteStore, sets DB_PATH,
and implements _schema(). All connection management + row_factory lives
here so feature code never deals with raw sqlite3 setup.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator


class SqliteStore:
    """Base class for a SQLite-backed store.

    Subclasses must set ``DB_PATH`` (or pass it to ``__init__``) and implement
    ``_schema(conn)`` to create/migrate tables. ``init()`` is idempotent.
    """

    DB_PATH: str = ""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path:
            self.DB_PATH = db_path
        if not self.DB_PATH:
            raise ValueError(
                f"{type(self).__name__}: DB_PATH not set"
            )
        self._initialized = False

    def _ensure_dir(self) -> None:
        d = os.path.dirname(self.DB_PATH)
        if d:
            os.makedirs(d, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context-managed connection with row_factory=Row.

        The connection is committed on success, rolled back on exception,
        and always closed.
        """
        self._ensure_dir()
        conn = sqlite3.connect(self.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        """Create tables if they don't exist. Safe to call repeatedly."""
        if self._initialized:
            return
        with self.connect() as c:
            self._schema(c)
        self._initialized = True

    def _schema(self, conn: sqlite3.Connection) -> None:  # pragma: no cover
        raise NotImplementedError(
            f"{type(self).__name__} must implement _schema(conn)"
        )
