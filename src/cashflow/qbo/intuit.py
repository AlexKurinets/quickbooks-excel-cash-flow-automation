"""QuickBooks Online over HTTP: OAuth 2.0 token rotation, the Reports API and the Account query.

Intuit issues a new refresh token on every refresh. If two runs for the same client refresh at
the same moment, the one that writes last can store a token Intuit has already replaced, and the
client is disconnected until someone reconnects it by hand. So a refresh takes `select ... for
update` on the client's token row, re-checks whether another process refreshed while it waited,
and writes the new pair in the same short transaction on its own connection.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from .. import db
from .source import ConnectionExpired, QuickBooksError

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_BASE = {"sandbox": "https://sandbox-quickbooks.api.intuit.com", "production": "https://quickbooks.api.intuit.com"}
MINOR_VERSION = "75"
PAGE = 1000


class IntuitQuickBooks:
    name = "intuit"

    def __init__(self, client_id: str, client_secret: str, environment: str = "sandbox",
                 http: httpx.Client | None = None, connect: Callable = db.connect,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base = API_BASE[environment]
        self.http = http or httpx.Client(timeout=60)
        self.connect = connect
        self.clock = clock
        self.sleep = sleep

    # -- tokens ---------------------------------------------------------------------------------

    def access_token(self, client_id: str, force: bool = False) -> str:
        expired = False
        with self.connect() as conn:
            row = conn.execute("select * from qbo_tokens where client_id = %s for update", (client_id,)).fetchone()
            if row is None:
                raise ConnectionExpired(None)
            now = self.clock()
            if not force and row["access_expires_at"] > now + timedelta(seconds=60):
                return row["access_token"]
            if row["refresh_expires_at"] <= now:
                expired = True
            else:
                resp = self.http.post(TOKEN_URL, auth=(self.client_id, self.client_secret),
                                      headers={"Accept": "application/json"},
                                      data={"grant_type": "refresh_token", "refresh_token": row["refresh_token"]})
                if resp.status_code in (400, 401) and resp.json().get("error") == "invalid_grant":
                    expired = True
                else:
                    resp.raise_for_status()
                    body = resp.json()
                    conn.execute(
                        """update qbo_tokens set access_token = %s, access_expires_at = %s, refresh_token = %s,
                                  refresh_expires_at = %s, refreshed_at = %s where client_id = %s""",
                        (body["access_token"], now + timedelta(seconds=int(body["expires_in"])), body["refresh_token"],
                         now + timedelta(seconds=int(body["x_refresh_token_expires_in"])), now, client_id))
                    return body["access_token"]
        if expired:
            with self.connect() as conn:
                conn.execute("update clients set connection_status = 'expired', connection_expired_at = %s "
                             "where id = %s", (self.clock(), client_id))
            raise ConnectionExpired(self.clock())
        raise AssertionError("unreachable")

    # -- requests -------------------------------------------------------------------------------

    def _get(self, client: dict, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base}/v3/company/{client['realm_id']}/{path}"
        params = {**params, "minorversion": MINOR_VERSION}
        token = self.access_token(client["id"])
        refreshed = False
        for attempt in range(4):
            resp = self.http.get(url, params=params, headers={"Authorization": f"Bearer {token}",
                                                              "Accept": "application/json"})
            if resp.status_code == 401 and not refreshed:
                token, refreshed = self.access_token(client["id"], force=True), True
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                self.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise QuickBooksError(f"{path}: HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            if "Fault" in body:
                raise QuickBooksError(f"{path}: {body['Fault']}")
            return body
        raise QuickBooksError(f"{path}: gave up after repeated throttling or server errors")

    def check_connection(self, conn, client: dict) -> None:
        if client["connection_status"] != "connected":
            raise ConnectionExpired(client.get("connection_expired_at"))
        self.access_token(client["id"])

    def accounts(self, conn, client: dict, as_of: datetime) -> dict:
        found, start = [], 1
        while True:
            body = self._get(client, "query", {"query": f"select * from Account startposition {start} maxresults {PAGE}"})
            page = body.get("QueryResponse", {}).get("Account", [])
            found += page
            if len(page) < PAGE:
                return {"QueryResponse": {"Account": found, "startPosition": 1}}
            start += PAGE

    def report(self, conn, client: dict, name: str, params: dict[str, str], as_of: datetime) -> dict:
        return self._get(client, f"reports/{name}", {**params, "summarize_column_by": "Month",
                                                     "accounting_method": client.get("basis", "Accrual")})
