"""Postgres access. Every connection works inside the `cashflow` schema."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import HOME, settings

SQL_DIR = HOME / "sql"


@contextmanager
def connect(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn or settings().dsn, row_factory=dict_row, options="-c search_path=cashflow,public")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(conn: psycopg.Connection, reset: bool = False) -> list[str]:
    if reset:
        conn.execute("drop schema if exists cashflow cascade")
    applied = []
    for path in sorted(Path(SQL_DIR).glob("*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    conn.commit()
    return applied


def audit(conn: psycopg.Connection, actor: str, action: str, client_id: str | None, detail: dict,
          at: datetime | None = None) -> None:
    conn.execute(
        "insert into audit_log (at, actor, action, client_id, detail) values (%s, %s, %s, %s, %s)",
        (at or datetime.now(UTC), actor, action, client_id, Jsonb(detail)),
    )
