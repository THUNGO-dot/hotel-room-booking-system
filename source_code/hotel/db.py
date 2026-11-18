"""Data-access layer: one SQLite connection with explicit transactions (NFR-07)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


class Database:
    """Thin wrapper around sqlite3 that adds nestable transactions.

    A booking + its payment, or a check-out + its invoice, run inside one
    transaction. If anything fails the whole unit is rolled back, so no
    half-saved record can remain (NFR-07).
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        # isolation_level=None -> we issue BEGIN/COMMIT ourselves
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._depth = 0

    # -- queries ---------------------------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def one(self, sql: str, params: tuple | list = ()):
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | list = ()):
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: tuple | list = ()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def insert(self, sql: str, params: tuple | list = ()) -> int:
        return self.conn.execute(sql, params).lastrowid

    # -- transactions ----------------------------------------------------
    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE takes the write lock up front, so two people booking
        the last room at the same moment are serialised (UC-04 exception 4a)."""
        outer = self._depth == 0
        if outer:
            self.conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if outer:
                self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if outer:
                self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()

    # -- schema ----------------------------------------------------------
    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    def has_schema(self) -> bool:
        return bool(self.scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account'"))
