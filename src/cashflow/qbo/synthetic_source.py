"""A QuickBooks Online stand-in that answers in the Reports API's JSON shape.

It serves BalanceSheet, ProfitAndLoss and CashFlow summarized by month, and the Account query,
from the synthetic books in `cashflow.synthetic`, as of any posting timestamp. The pipeline cannot
tell it apart from `IntuitQuickBooks` except that the data is invented.

Retained earnings follow QuickBooks' convention: the balance sheet shows prior years' profit in
Retained Earnings and the current fiscal year's profit on a computed Net Income row, so on
1 January the prior year's net income moves from one to the other without any journal entry.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..synthetic import Company, month_end, months, world
from .source import ConnectionExpired

BS_TYPES = {"Asset", "Liability", "Equity"}


def _fmt(cents: int, blank_zero: bool = False) -> str:
    if blank_zero and cents == 0:
        return ""
    return str(Decimal(cents).scaleb(-2))


def _columns(month_list: list[date], total: bool) -> list[dict]:
    cols = [{"ColTitle": "", "ColType": "Account", "MetaData": [{"Name": "ColKey", "Value": "account"}]}]
    for m in month_list:
        title = m.strftime("%b %Y")
        cols.append({"ColTitle": title, "ColType": "Money", "MetaData": [
            {"Name": "StartDate", "Value": m.isoformat()},
            {"Name": "EndDate", "Value": month_end(m).isoformat()},
            {"Name": "ColKey", "Value": title}]})
    if total:
        cols.append({"ColTitle": "Total", "ColType": "Money", "MetaData": [{"Name": "ColKey", "Value": "total"}]})
    return cols


def _data(label: str, values: list[int], account_id: str | None = None, blank_zero: bool = False,
          group: str | None = None, total: bool = False) -> dict:
    first = {"value": label}
    if account_id:
        first["id"] = account_id
    cells = [first] + [{"value": _fmt(v, blank_zero)} for v in values]
    if total:
        cells.append({"value": _fmt(sum(values), blank_zero)})
    row = {"ColData": cells, "type": "Data"}
    if group:
        row["group"] = group
    return row


def _section(group: str, header: str | None, rows: list[dict], summary_label: str | None, totals: list[int] | None,
             total: bool = False) -> dict:
    section: dict = {"type": "Section", "group": group}
    if header is not None:
        section["Header"] = {"ColData": [{"value": header}] + [{"value": ""} for _ in (totals or [])]}
    if rows:
        section["Rows"] = {"Row": rows}
    if summary_label is not None and totals is not None:
        cells = [{"value": summary_label}] + [{"value": _fmt(v)} for v in totals]
        if total:
            cells.append({"value": _fmt(sum(totals))})
        section["Summary"] = {"ColData": cells}
    return section


def _add(a: list[int], b: list[int]) -> list[int]:
    return [x + y for x, y in zip(a, b, strict=True)]


class _Books:
    """Balances and activity for one company as of one moment."""

    def __init__(self, company: Company, as_of: datetime) -> None:
        self.company = company
        self.accounts = {a.id: a for a in company.accounts_as_of(as_of)}
        self.entries = company.entries_as_of(as_of)  # every synthetic client keeps a calendar fiscal year

    def balance(self, account_id: str, on: date) -> int:
        """Debit-positive balance from posted entries dated on or before `on`."""
        return sum(v for e in self.entries if e.date <= on for a, v in e.lines if a == account_id)

    def activity(self, start: date, end: date) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for e in self.entries:
            if start <= e.date <= end:
                for a, v in e.lines:
                    out[a] += v
        return out

    def net_income(self, start: date, end: date) -> int:
        act = self.activity(start, end)
        return -sum(v for a, v in act.items() if self.accounts[a].classification not in BS_TYPES)

    def reported(self, account_id: str, on: date) -> int:
        """Balance as the balance sheet shows it: assets debit-positive, everything else credit-positive."""
        acct = self.accounts[account_id]
        bal = self.balance(account_id, on)
        if acct.classification == "Asset":
            return bal
        value = -bal
        if acct.subtype == "RetainedEarnings":
            value += self.net_income(date(1900, 1, 1), date(on.year - 1, 12, 31))
        return value


def _bs_rows(books: _Books, account_type: str, ends: list[date]) -> tuple[list[dict], list[int]]:
    rows, totals = [], [0] * len(ends)
    for acct in books.accounts.values():
        if acct.type == account_type:
            values = [books.reported(acct.id, e) for e in ends]
            if any(values):
                rows.append(_data(acct.name, values, acct.id))
                totals = _add(totals, values)
    return rows, totals


def balance_sheet(books: _Books, start: date, end: date) -> dict:
    month_list = months(start, end)
    ends = [month_end(m) for m in month_list]
    bank, bank_t = _bs_rows(books, "Bank", ends)
    ar, ar_t = _bs_rows(books, "Accounts Receivable", ends)
    oca, oca_t = _bs_rows(books, "Other Current Asset", ends)
    fixed, fixed_t = _bs_rows(books, "Fixed Asset", ends)
    other, other_t = _bs_rows(books, "Other Asset", ends)
    ap, ap_t = _bs_rows(books, "Accounts Payable", ends)
    cc, cc_t = _bs_rows(books, "Credit Card", ends)
    ocl, ocl_t = _bs_rows(books, "Other Current Liability", ends)
    ltl, ltl_t = _bs_rows(books, "Long Term Liability", ends)
    eq, eq_t = _bs_rows(books, "Equity", ends)
    ni = [books.net_income(date(e.year, 1, 1), e) for e in ends]
    eq.append(_data("Net Income", ni, group="NetIncome"))
    eq_t = _add(eq_t, ni)

    current_assets = _add(_add(bank_t, ar_t), oca_t)
    assets = _add(_add(current_assets, fixed_t), other_t)
    current_liab = _add(_add(ap_t, cc_t), ocl_t)
    liabilities = _add(current_liab, ltl_t)
    rows = [
        _section("TotalAssets", "ASSETS", [
            _section("CurrentAssets", "Current Assets", [
                _section("BankAccounts", "Bank Accounts", bank, "Total Bank Accounts", bank_t),
                _section("AR", "Accounts Receivable", ar, "Total Accounts Receivable", ar_t),
                _section("OtherCurrentAssets", "Other Current Assets", oca, "Total Other Current Assets", oca_t),
            ], "Total Current Assets", current_assets),
            _section("FixedAssets", "Fixed Assets", fixed, "Total Fixed Assets", fixed_t),
            _section("OtherAssets", "Other Assets", other, "Total Other Assets", other_t),
        ], "TOTAL ASSETS", assets),
        _section("TotalLiabilitiesAndEquity", "LIABILITIES AND EQUITY", [
            _section("Liabilities", "Liabilities", [
                _section("CurrentLiabilities", "Current Liabilities", [
                    _section("AP", "Accounts Payable", ap, "Total Accounts Payable", ap_t),
                    _section("CreditCards", "Credit Cards", cc, "Total Credit Cards", cc_t),
                    _section("OtherCurrentLiabilities", "Other Current Liabilities", ocl,
                             "Total Other Current Liabilities", ocl_t),
                ], "Total Current Liabilities", current_liab),
                _section("LongTermLiabilities", "Long-Term Liabilities", ltl, "Total Long-Term Liabilities", ltl_t),
            ], "Total Liabilities", liabilities),
            _section("Equity", "Equity", eq, "Total Equity", eq_t),
        ], "TOTAL LIABILITIES AND EQUITY", _add(liabilities, eq_t)),
    ]
    return {"Header": _header("BalanceSheet", start, end), "Columns": {"Column": _columns(month_list, False)},
            "Rows": {"Row": rows}}


def profit_and_loss(books: _Books, start: date, end: date) -> dict:
    month_list = months(start, end)
    acts = [books.activity(m, month_end(m)) for m in month_list]

    def rows_for(types: set[str], sign: int) -> tuple[list[dict], list[int]]:
        rows, totals = [], [0] * len(month_list)
        for acct in books.accounts.values():
            if acct.type in types:
                values = [sign * a.get(acct.id, 0) for a in acts]
                if any(values):
                    rows.append(_data(acct.name, values, acct.id, blank_zero=True, total=True))
                    totals = _add(totals, values)
        return rows, totals

    inc, inc_t = rows_for({"Income"}, -1)
    cogs, cogs_t = rows_for({"Cost of Goods Sold"}, 1)
    exp, exp_t = rows_for({"Expense"}, 1)
    oexp, oexp_t = rows_for({"Other Expense"}, 1)
    gross = [a - b for a, b in zip(inc_t, cogs_t, strict=True)]
    noi = [a - b for a, b in zip(gross, exp_t, strict=True)]
    net = [a - b for a, b in zip(noi, oexp_t, strict=True)]
    rows = [
        _section("Income", "Income", inc, "Total Income", inc_t, total=True),
        _section("COGS", "Cost of Goods Sold", cogs, "Total Cost of Goods Sold", cogs_t, total=True),
        _section("GrossProfit", None, [], "Gross Profit", gross, total=True),
        _section("Expenses", "Expenses", exp, "Total Expenses", exp_t, total=True),
        _section("NetOperatingIncome", None, [], "Net Operating Income", noi, total=True),
        _section("OtherExpenses", "Other Expenses", oexp, "Total Other Expenses", oexp_t, total=True),
        _section("NetOtherIncome", None, [], "Net Other Income", [-v for v in oexp_t], total=True),
        _section("NetIncome", None, [], "Net Income", net, total=True),
    ]
    return {"Header": _header("ProfitAndLoss", start, end), "Columns": {"Column": _columns(month_list, True)},
            "Rows": {"Row": rows}}


def _cf_bucket(acct) -> str | None:
    if acct.type == "Bank":
        return None
    if acct.type in {"Accounts Receivable", "Other Current Asset", "Accounts Payable", "Credit Card"}:
        return "operating"
    if acct.type == "Other Current Liability":
        return "financing" if acct.subtype == "LineOfCredit" else "operating"
    if acct.type == "Fixed Asset":
        return "operating" if acct.subtype == "AccumulatedDepreciation" else "investing"
    if acct.type == "Other Asset":
        return "investing"
    if acct.type in {"Long Term Liability", "Equity"}:
        return "financing"
    return None


def cash_flow(books: _Books, start: date, end: date) -> dict:
    """QuickBooks' own Statement of Cash Flows. Cash means Bank-type accounts."""
    month_list = months(start, end)
    n = len(month_list)
    buckets: dict[str, list[dict]] = {"operating": [], "investing": [], "financing": []}
    totals = {k: [0] * n for k in buckets}
    for acct in books.accounts.values():
        bucket = _cf_bucket(acct)
        if bucket is None:
            continue
        # Cash effect of any non-cash account is minus its debit-positive movement. Using the ledger
        # movement rather than reported balances leaves out the 1 January retained earnings roll.
        values = [-books.activity(m, month_end(m)).get(acct.id, 0) for m in month_list]
        if any(values):
            buckets[bucket].append(_data(acct.name, values, acct.id, blank_zero=True, total=True))
            totals[bucket] = _add(totals[bucket], values)
    ni = [books.net_income(m, month_end(m)) for m in month_list]
    operating = _add(ni, totals["operating"])
    increase = _add(_add(operating, totals["investing"]), totals["financing"])
    banks = [a.id for a in books.accounts.values() if a.type == "Bank"]
    begin = [sum(books.balance(b, m - timedelta(days=1)) for b in banks) for m in month_list]
    ending = [sum(books.balance(b, month_end(m)) for b in banks) for m in month_list]
    rows = [
        _section("OperatingActivities", "OPERATING ACTIVITIES", [
            _data("Net Income", ni, total=True),
            _section("OperatingAdjustments",
                     "Adjustments to reconcile Net Income to Net Cash provided by operations:",
                     buckets["operating"],
                     "Total Adjustments to reconcile Net Income to Net Cash provided by operations:",
                     totals["operating"], total=True),
        ], "Net cash provided by operating activities", operating, total=True),
        _section("InvestingActivities", "INVESTING ACTIVITIES", buckets["investing"],
                 "Net cash provided by investing activities", totals["investing"], total=True),
        _section("FinancingActivities", "FINANCING ACTIVITIES", buckets["financing"],
                 "Net cash provided by financing activities", totals["financing"], total=True),
        _section("CashIncrease", None, [], "Net cash increase for period", increase, total=True),
        _section("BeginningCash", None, [], "Cash at beginning of period", begin),
        _section("EndingCash", None, [], "Cash at end of period", ending),
    ]
    return {"Header": _header("CashFlow", start, end), "Columns": {"Column": _columns(month_list, True)},
            "Rows": {"Row": rows}}


def _header(name: str, start: date, end: date) -> dict:
    return {"ReportName": name, "ReportBasis": "Accrual", "StartPeriod": start.isoformat(),
            "EndPeriod": end.isoformat(), "SummarizeColumnsBy": "Month", "Currency": "USD",
            "Option": [{"Name": "NoReportData", "Value": "false"}]}


REPORTS = {"BalanceSheet": balance_sheet, "ProfitAndLoss": profit_and_loss, "CashFlow": cash_flow}


class SyntheticQuickBooks:
    name = "synthetic"

    def __init__(self, companies: dict[str, Company] | None = None) -> None:
        self.companies = companies or world()

    def _company(self, client: dict) -> Company:
        return self.companies[client["id"]]

    def check_connection(self, conn, client: dict) -> None:
        if client["connection_status"] != "connected":
            raise ConnectionExpired(client.get("connection_expired_at"))

    def accounts(self, conn, client: dict, as_of: datetime) -> dict:
        company = self._company(client)
        return {"QueryResponse": {"Account": [
            {"Id": a.id, "Name": a.name, "FullyQualifiedName": a.name, "AccountType": a.type,
             "AccountSubType": a.subtype, "Classification": a.classification, "Active": True,
             "MetaData": {"CreateTime": a.created.isoformat()}}
            for a in company.accounts_as_of(as_of)], "startPosition": 1}}

    def report(self, conn, client: dict, name: str, params: dict[str, str], as_of: datetime) -> dict:
        books = _Books(self._company(client), as_of)
        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        return REPORTS[name](books, start, end)
