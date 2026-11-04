"""SQLite for local use and PostgreSQL for hosted deployments."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class HybridRow(dict):
    """Postgres row supporting both named and positional indexing."""
    def __init__(self, cursor, values):
        super().__init__((column.name, value) for column, value in zip(cursor.description, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def connect(db_path=None):
    """Return a DB-API-ish connection used by the existing engine."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        db = Path(db_path or os.environ.get("EIS_DB", Path(__file__).parent / "data" / "eis.db"))
        db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    import psycopg
    from psycopg.rows import set_row_factory

    c = psycopg.connect(url, connect_timeout=10)
    set_row_factory(c, lambda cursor: lambda values: HybridRow(cursor, values))
    return PostgresConnection(c)


class PostgresConnection:
    def __init__(self, connection):
        self.raw = connection
        self.total_changes = 0

    def execute(self, sql, params=()):
        # The application SQL uses SQLite's positional marker. Translate it for psycopg.
        sql = sql.replace("?", "%s")
        cur = self.raw.cursor()
        cur.execute(sql, params)
        if cur.rowcount > 0:
            self.total_changes += cur.rowcount
        return cur

    def executescript(self, sql):
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()
