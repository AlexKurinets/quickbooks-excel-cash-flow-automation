"""Load the synthetic firm: eight clients, their onboarding mappings, and two month-end closes.

The August close runs against the books as they stood on 8 September and every workbook is marked
as sent the next day. Then Rye & Ember's QuickBooks connection expires on 2 October, and the
September close runs against the books as of 8 October. That leaves the stack in the state a
reviewer would find on the morning after the September close.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg

from . import db, mapping, runner
from .qbo.source import QuickBooksSource
from .synthetic import PROFILES, world

ONBOARDED_AT = datetime(2026, 1, 8, 15, 0, tzinfo=UTC)
AUGUST = (date(2026, 8, 31), datetime(2026, 9, 8, 9, 0, tzinfo=UTC))
SEPTEMBER = (date(2026, 9, 30), datetime(2026, 10, 8, 9, 0, tzinfo=UTC))
SENT_AT = datetime(2026, 9, 9, 14, 0, tzinfo=UTC)
EXPIRED_AT = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
PREPARER = "devin.holloway@example.com"
REVIEWER = "mara.quist@example.com"


def onboard(conn: psycopg.Connection) -> int:
    rules = mapping.RulesSuggester()
    count = 0
    for p in PROFILES:
        conn.execute("insert into clients (id, name, industry, realm_id, created_at) values (%s, %s, %s, %s, %s)",
                     (p.id, p.name, p.industry, p.realm_id, ONBOARDED_AT))
        for acct in world()[p.id].accounts_as_of(ONBOARDED_AT):
            if acct.classification not in {"Asset", "Liability", "Equity"}:
                continue
            s = rules.suggest({"name": acct.name, "account_type": acct.type, "account_subtype": acct.subtype}, p.industry)
            conn.execute("""insert into account_mappings (client_id, account_id, line_key, source, approved_by,
                                approved_at) values (%s, %s, %s, 'onboarding', %s, %s)""",
                         (p.id, acct.id, s.line_key, REVIEWER, ONBOARDED_AT))
            count += 1
    db.audit(conn, REVIEWER, "onboarding", None, {"clients": len(PROFILES), "mappings": count}, ONBOARDED_AT)
    conn.commit()
    return count


def close(conn: psycopg.Connection, period: date, as_of: datetime, source: QuickBooksSource | None = None,
          **kwargs) -> list[int]:
    ids = []
    for i, p in enumerate(PROFILES):
        ids.append(runner.run_client(conn, p.id, period, actor=PREPARER, as_of=as_of,
                                     now=as_of + timedelta(minutes=2 * i), source=source, **kwargs))
    return ids


def seed(conn: psycopg.Connection, source: QuickBooksSource | None = None, **kwargs) -> dict:
    if conn.execute("select count(*) as n from clients").fetchone()["n"]:
        return {"seeded": False}
    mappings = onboard(conn)
    august = close(conn, *AUGUST, source=source, **kwargs)
    for run_id in august:
        runner.deliver(conn, run_id, REVIEWER, SENT_AT)
    conn.execute("update clients set connection_status = 'expired', connection_expired_at = %s where id = %s",
                 (EXPIRED_AT, "rye-and-ember-bakery"))
    conn.commit()
    september = close(conn, *SEPTEMBER, source=source, **kwargs)
    return {"seeded": True, "clients": len(PROFILES), "mappings": mappings, "august_runs": august,
            "september_runs": september}
