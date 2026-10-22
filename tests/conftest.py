"""Tests run against a real Postgres at CASHFLOW_DSN; each fixture rebuilds the schema it uses."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("CASHFLOW_DSN", "postgresql://cashflow:cashflow@localhost:5432/cashflow")
os.environ["CASHFLOW_SOURCE"] = "synthetic"
os.environ["CASHFLOW_SUGGESTER"] = "rules"
os.environ.setdefault("TQDM_DISABLE", "1")

from cashflow import db, seed  # noqa: E402
from cashflow.config import HOME  # noqa: E402

DSN = os.environ["CASHFLOW_DSN"]
TEMPLATE = HOME / "templates" / "monthly-cash-flow.xlsx"
MAP = HOME / "templates" / "monthly-cash-flow.map.yml"


@pytest.fixture
def conn():
    with db.connect(DSN) as c:
        db.migrate(c, reset=True)
        yield c


@pytest.fixture(scope="module")
def seeded():
    """The stack's starting state: onboarding, the August close (sent) and the September close."""
    with db.connect(DSN) as c:
        db.migrate(c, reset=True)
        result = seed.seed(c)
        yield {"conn": c, **result}


@pytest.fixture
def template_bytes() -> bytes:
    return Path(TEMPLATE).read_bytes()


def latest(conn, client_id: str, period: str = "2026-09-30") -> dict:
    return conn.execute("select * from runs where client_id = %s and period = %s order by id desc limit 1",
                        (client_id, period)).fetchone()


def checks(conn, run_id: int) -> dict[str, dict]:
    rows = conn.execute("select * from run_checks where run_id = %s order by ordinal", (run_id,)).fetchall()
    return {r["key"]: r for r in rows}
