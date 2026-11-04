"""SQLite for local use and PostgreSQL for hosted deployments."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

POSTGRES_ENV_NAMES = ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_URL_NON_POOLING", "POSTGRES_PRISMA_URL")


def postgres_url():
    # Prefer standard names; Marketplace integrations can prefix variables with
    # their resource name (for example PURPLEFIELD_DATABASE_URL).
    for name in POSTGRES_ENV_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    # Prefer a prefixed DATABASE_URL over a prefixed POSTGRES_URL. Marketplace
    # resources may expose both names, and they can target different endpoints.
    for base_name in POSTGRES_ENV_NAMES:
        suffix = "_" + base_name
        for name in sorted(os.environ):
            if name.endswith(suffix) and os.environ[name]:
                return os.environ[name]
    return None


def postgres_url_name():
    for name in POSTGRES_ENV_NAMES:
        if os.environ.get(name):
            return name
    for base_name in POSTGRES_ENV_NAMES:
        suffix = "_" + base_name
        for name in sorted(os.environ):
            if name.endswith(suffix) and os.environ[name]:
                return name
    return None


def has_postgres_url():
    return bool(postgres_url())


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
    url = postgres_url()
    if not url:
        db = Path(db_path or os.environ.get("EIS_DB", Path(__file__).parent / "data" / "eis.db"))
        if os.environ.get("VERCEL") or str(db).replace("\\", "/").startswith("/var/task/"):
            raise RuntimeError(
                "PostgreSQL is not connected. Add DATABASE_URL (or POSTGRES_URL) to this Vercel environment and redeploy."
            )
        db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    import psycopg

    c = psycopg.connect(url, connect_timeout=10)
    c.row_factory = lambda cursor: lambda values: HybridRow(cursor, values)
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
