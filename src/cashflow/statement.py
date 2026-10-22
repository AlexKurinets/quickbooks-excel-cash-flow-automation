"""The indirect-method statement of cash flows, computed from QuickBooks reports and approved mappings.

Net income comes from the P&L. Every other line is the month's change in the balance sheet
accounts mapped to it: an asset that grows used cash, so its change counts negative; a liability or
equity account that grows supplied cash, so its change counts positive. Accumulated depreciation is
a contra asset, so the same rule turns its monthly growth into the add-back.

The balance sheet request starts one month before the fiscal year so January has an opening
balance. Retained earnings and the balance sheet's computed Net Income row are left out of the
lines (net income already covers them) and are used instead to check that equity moved only by net
income. A direct posting to retained earnings breaks that, and it would break the cash flow too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .lines import FLOW_KEYS
from .qbo.reports import Report

ZERO = Decimal("0")


@dataclass
class Statement:
    month_ends: list[date]
    net_income: list[Decimal]
    lines: dict[str, list[Decimal]]
    cash_begin: Decimal
    cash_end: list[Decimal]
    equity_change: list[Decimal]
    unmapped: list[dict] = field(default_factory=list)
    by_account: dict[str, dict] = field(default_factory=dict)


def build(balance_sheet: Report, profit_and_loss: Report, accounts: dict[str, dict], mapping: dict[str, str]
          ) -> Statement:
    ends = profit_and_loss.month_ends
    bs_ends = balance_sheet.month_ends
    if bs_ends[1:] != ends:
        raise ValueError(f"balance sheet columns {bs_ends} do not line up with P&L columns {ends}")
    n = len(ends)
    lines = {key: [ZERO] * n for key in FLOW_KEYS}
    cash = [ZERO] * (n + 1)
    equity = [ZERO] * (n + 1)
    net_income_row = balance_sheet.groups.get("NetIncome")
    if net_income_row:
        equity = [a + b for a, b in zip(equity, net_income_row, strict=True)]
    st = Statement(ends, list(profit_and_loss.group("NetIncome")), lines, ZERO, [], [])

    for account_id, row in balance_sheet.accounts.items():
        acct = accounts.get(account_id, {"name": row.name, "account_type": "?", "account_subtype": "?",
                                         "classification": "Asset" if row.path[:1] == ("TotalAssets",) else "Liability"})
        key = mapping.get(account_id)
        if key is None:
            if any(row.values):
                st.unmapped.append({"account_id": account_id, **acct})
            continue
        if key == "cash":
            cash = [a + b for a, b in zip(cash, row.values, strict=True)]
            continue
        if key == "equity_closing":
            equity = [a + b for a, b in zip(equity, row.values, strict=True)]
            continue
        sign = -1 if acct["classification"] == "Asset" else 1
        effects = [sign * (row.values[i + 1] - row.values[i]) for i in range(n)]
        lines[key] = [a + b for a, b in zip(lines[key], effects, strict=True)]
        st.by_account[account_id] = {"name": acct["name"], "line": key, "effects": effects}

    st.cash_begin = cash[0]
    st.cash_end = cash[1:]
    st.equity_change = [equity[i + 1] - equity[i] for i in range(n)]
    return st


def net_change(st: Statement) -> list[Decimal]:
    return [st.net_income[i] + sum(v[i] for v in st.lines.values()) for i in range(len(st.month_ends))]
