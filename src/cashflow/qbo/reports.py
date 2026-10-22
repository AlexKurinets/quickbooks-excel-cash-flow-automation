"""Parse QuickBooks Online Reports API JSON (ProfitAndLoss, BalanceSheet, CashFlow).

The reports are nested sections of rows. An account row carries the account's ID in its first
column (`ColData[0].id`), which is what mappings are keyed on, so renaming an account in QuickBooks
never moves it to a different line. Summary rows are identified by their `group`, for example
`NetIncome` on the P&L or `CashIncrease` on the cash flow report. Columns come from
`summarize_column_by=Month`; each month column carries StartDate and EndDate metadata, and the
trailing Total column is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class AccountRow:
    account_id: str
    name: str
    path: tuple[str, ...]          # section groups above the row, outermost first
    values: tuple[Decimal, ...]


@dataclass
class Report:
    name: str
    month_ends: list[date]
    accounts: dict[str, AccountRow] = field(default_factory=dict)
    groups: dict[str, tuple[Decimal, ...]] = field(default_factory=dict)
    labels: dict[str, tuple[Decimal, ...]] = field(default_factory=dict)

    def column(self, end: date) -> int:
        return self.month_ends.index(end)

    def group(self, key: str) -> tuple[Decimal, ...]:
        if key not in self.groups:
            raise KeyError(f"{self.name} report has no {key!r} row")
        return self.groups[key]


def money(text: str | None) -> Decimal:
    text = (text or "").replace(",", "").strip()
    return Decimal(text) if text else Decimal("0")


def parse(payload: dict) -> Report:
    header = payload.get("Header", {})
    columns = payload["Columns"]["Column"]
    keep: list[int] = []
    ends: list[date] = []
    for i, col in enumerate(columns):
        meta = {m["Name"]: m["Value"] for m in col.get("MetaData", [])}
        if col.get("ColType") == "Money" and "EndDate" in meta:
            keep.append(i)
            ends.append(date.fromisoformat(meta["EndDate"]))
    report = Report(name=header.get("ReportName", "Report"), month_ends=ends)

    def values(coldata: list[dict]) -> tuple[Decimal, ...]:
        return tuple(money(coldata[i].get("value")) if i < len(coldata) else Decimal("0") for i in keep)

    def walk(rows: list[dict], path: tuple[str, ...]) -> None:
        for row in rows:
            group = row.get("group")
            if row.get("type") == "Section" or "Rows" in row or "Summary" in row:
                inner = path + ((group,) if group else ())
                walk(row.get("Rows", {}).get("Row", []), inner)
                summary = row.get("Summary", {}).get("ColData")
                if summary:
                    if group:
                        report.groups[group] = values(summary)
                    report.labels[summary[0].get("value", "")] = values(summary)
                continue
            coldata = row.get("ColData", [])
            if not coldata:
                continue
            first = coldata[0]
            if first.get("id"):
                report.accounts[first["id"]] = AccountRow(first["id"], first.get("value", ""), path, values(coldata))
            else:
                if group:
                    report.groups[group] = values(coldata)
                report.labels[first.get("value", "")] = values(coldata)

    walk(payload.get("Rows", {}).get("Row", []), ())
    return report
