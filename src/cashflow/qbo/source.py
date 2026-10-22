"""Where QuickBooks data comes from. The pipeline only ever talks to this interface."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ConnectionExpired(Exception):
    """The client company's QuickBooks authorization is gone; someone with admin access must reconnect."""

    def __init__(self, expired_at: datetime | None) -> None:
        super().__init__(f"QuickBooks connection expired at {expired_at}")
        self.expired_at = expired_at


class QuickBooksError(Exception):
    pass


class QuickBooksSource(Protocol):
    name: str

    def check_connection(self, conn, client: dict) -> None: ...

    def accounts(self, conn, client: dict, as_of: datetime) -> dict: ...

    def report(self, conn, client: dict, name: str, params: dict[str, str], as_of: datetime) -> dict: ...


def make_source(kind: str | None = None) -> QuickBooksSource:
    from ..config import settings

    s = settings()
    kind = kind or s.source
    if kind == "synthetic":
        from .synthetic_source import SyntheticQuickBooks

        return SyntheticQuickBooks()
    if kind == "intuit":
        from .intuit import IntuitQuickBooks

        return IntuitQuickBooks(s.intuit_client_id, s.intuit_client_secret, s.intuit_environment)
    raise ValueError(f"unknown CASHFLOW_SOURCE {kind!r}")
