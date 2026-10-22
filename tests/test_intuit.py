"""The HTTP adapter against a mock transport: token rotation, retries, request shape, expiry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cashflow import db
from cashflow.qbo.intuit import TOKEN_URL, IntuitQuickBooks
from cashflow.qbo.source import ConnectionExpired
from conftest import DSN

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
def client(conn):
    conn.execute("insert into clients (id, name, industry, realm_id) values ('c1', 'Example Co', 'Bakery', '9130001')")
    conn.execute("""insert into qbo_tokens (client_id, access_token, access_expires_at, refresh_token, refresh_expires_at)
                    values ('c1', 'old-access', %s, 'refresh-1', %s)""", (NOW - timedelta(minutes=5), NOW + timedelta(days=60)))
    conn.commit()
    return conn.execute("select * from clients where id = 'c1'").fetchone()


class Intuit:
    """A tiny fake of the token endpoint and the accounting API."""

    def __init__(self, report_statuses=(200,), refresh_error=None, pages=(3,)):
        self.calls: list[httpx.Request] = []
        self.report_statuses = list(report_statuses)
        self.refresh_error = refresh_error
        self.pages = list(pages)
        self.issued = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if str(request.url) == TOKEN_URL:
            if self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error})
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"access-{self.issued}", "expires_in": 3600,
                                             "refresh_token": f"refresh-{self.issued}",
                                             "x_refresh_token_expires_in": 8_640_000})
        if "/query" in request.url.path:
            n = self.pages.pop(0)
            return httpx.Response(200, json={"QueryResponse": {"Account": [{"Id": str(i)} for i in range(n)]}})
        status = self.report_statuses.pop(0) if self.report_statuses else 200
        if status != 200:
            return httpx.Response(status, json={"fault": "x"})
        return httpx.Response(200, json={"Header": {"ReportName": "BalanceSheet"}, "Columns": {"Column": []}})


def adapter(fake: Intuit, sleeps: list | None = None) -> IntuitQuickBooks:
    return IntuitQuickBooks("id", "secret", "sandbox", http=httpx.Client(transport=httpx.MockTransport(fake)),
                            connect=lambda: db.connect(DSN), clock=lambda: NOW,
                            sleep=(sleeps.append if sleeps is not None else lambda s: None))


def tokens(conn):
    conn.rollback()
    return conn.execute("select * from qbo_tokens where client_id = 'c1'").fetchone()


def test_expired_access_token_is_refreshed_and_the_rotated_refresh_token_is_stored(conn, client):
    fake = Intuit()
    adapter(fake).report(conn, client, "BalanceSheet", {"start_date": "2025-12-01", "end_date": "2026-09-30"}, NOW)
    refresh, report = fake.calls
    assert refresh.method == "POST" and b"refresh_token=refresh-1" in refresh.content
    assert refresh.headers["authorization"].startswith("Basic ")
    t = tokens(conn)
    assert (t["access_token"], t["refresh_token"]) == ("access-2", "refresh-2")
    assert t["access_expires_at"] == NOW + timedelta(hours=1)
    assert report.url.path == "/v3/company/9130001/reports/BalanceSheet"
    assert report.url.host == "sandbox-quickbooks.api.intuit.com"
    params = dict(report.url.params)
    assert params == {"start_date": "2025-12-01", "end_date": "2026-09-30", "summarize_column_by": "Month",
                      "accounting_method": "Accrual", "minorversion": "75"}
    assert report.headers["authorization"] == "Bearer access-2"


def test_a_fresh_token_is_reused_without_refreshing(conn, client):
    fake = Intuit()
    a = adapter(fake)
    a.report(conn, client, "ProfitAndLoss", {"start_date": "2026-01-01", "end_date": "2026-09-30"}, NOW)
    a.report(conn, client, "CashFlow", {"start_date": "2026-01-01", "end_date": "2026-09-30"}, NOW)
    assert sum(1 for c in fake.calls if str(c.url) == TOKEN_URL) == 1


def test_a_401_forces_one_refresh_and_a_retry(conn, client):
    fake = Intuit(report_statuses=[200])
    a = adapter(fake)
    a.access_token("c1")                         # first refresh: access-2
    fake.report_statuses = [401, 200]
    a.report(conn, client, "BalanceSheet", {"start_date": "2025-12-01", "end_date": "2026-09-30"}, NOW)
    assert tokens(conn)["refresh_token"] == "refresh-3"
    assert fake.calls[-1].headers["authorization"] == "Bearer access-3"


def test_throttling_backs_off_and_retries(conn, client):
    sleeps: list[float] = []
    fake = Intuit(report_statuses=[429, 503, 200])
    adapter(fake, sleeps).report(conn, client, "BalanceSheet", {"start_date": "2025-12-01", "end_date": "2026-09-30"}, NOW)
    assert sleeps == [1, 2]


def test_invalid_grant_marks_the_client_disconnected(conn, client):
    with pytest.raises(ConnectionExpired):
        adapter(Intuit(refresh_error="invalid_grant")).check_connection(conn, client)
    conn.rollback()
    row = conn.execute("select connection_status, connection_expired_at from clients where id = 'c1'").fetchone()
    assert row["connection_status"] == "expired" and row["connection_expired_at"] == NOW
    assert tokens(conn)["refresh_token"] == "refresh-1"


def test_account_query_pages_through_large_charts(conn, client, monkeypatch):
    monkeypatch.setattr("cashflow.qbo.intuit.PAGE", 3)
    fake = Intuit(pages=[3, 3, 1])
    found = adapter(fake).accounts(conn, client, NOW)["QueryResponse"]["Account"]
    assert len(found) == 7
    queries = [dict(c.url.params)["query"] for c in fake.calls if "/query" in c.url.path]
    assert queries == ["select * from Account startposition 1 maxresults 3",
                       "select * from Account startposition 4 maxresults 3",
                       "select * from Account startposition 7 maxresults 3"]
