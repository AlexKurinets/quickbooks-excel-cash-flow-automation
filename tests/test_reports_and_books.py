"""The report parser, the synthetic books behind it, and the indirect-method arithmetic."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from cashflow import mapping, statement
from cashflow.qbo.reports import parse
from cashflow.qbo.synthetic_source import SyntheticQuickBooks
from cashflow.synthetic import PROFILES, world

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
AUG = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)


def money_col(title, start, end):
    return {"ColTitle": title, "ColType": "Money", "MetaData": [{"Name": "StartDate", "Value": start},
                                                                 {"Name": "EndDate", "Value": end}]}


def test_parser_reads_nested_sections_ids_groups_and_drops_total():
    payload = {
        "Header": {"ReportName": "BalanceSheet"},
        "Columns": {"Column": [{"ColTitle": "", "ColType": "Account"},
                               money_col("Jan 2026", "2026-01-01", "2026-01-31"),
                               money_col("Feb 2026", "2026-02-01", "2026-02-28"),
                               {"ColTitle": "Total", "ColType": "Money", "MetaData": [{"Name": "ColKey", "Value": "total"}]}]},
        "Rows": {"Row": [
            {"type": "Section", "group": "TotalAssets", "Header": {"ColData": [{"value": "ASSETS"}]},
             "Rows": {"Row": [{"type": "Section", "group": "BankAccounts", "Rows": {"Row": [
                 {"type": "Data", "ColData": [{"value": "Checking", "id": "35"}, {"value": "1,200.50"},
                                              {"value": ""}, {"value": "999"}]}]},
                 "Summary": {"ColData": [{"value": "Total Bank Accounts"}, {"value": "1200.50"}, {"value": "0"}]}}]},
             "Summary": {"ColData": [{"value": "TOTAL ASSETS"}, {"value": "1200.50"}, {"value": "0.00"}]}},
            {"type": "Data", "group": "NetIncome", "ColData": [{"value": "Net Income"}, {"value": "-15.25"},
                                                               {"value": "4"}]},
        ]},
    }
    r = parse(payload)
    assert r.month_ends == [date(2026, 1, 31), date(2026, 2, 28)]
    row = r.accounts["35"]
    assert row.values == (Decimal("1200.50"), Decimal("0"))
    assert row.path == ("TotalAssets", "BankAccounts")
    assert r.group("TotalAssets") == (Decimal("1200.50"), Decimal("0"))
    assert r.group("NetIncome") == (Decimal("-15.25"), Decimal("4"))
    with pytest.raises(KeyError):
        r.group("CashIncrease")


def _client(p):
    return {"id": p.id, "realm_id": p.realm_id, "connection_status": "connected"}


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.id)
def test_every_client_balance_sheet_balances_and_cash_flow_report_reconciles(profile):
    src = SyntheticQuickBooks()
    client = _client(profile)
    bs = parse(src.report(None, client, "BalanceSheet", {"start_date": "2025-12-01", "end_date": "2026-09-30"}, NOW))
    assert bs.group("TotalAssets") == bs.group("TotalLiabilitiesAndEquity")
    cf = parse(src.report(None, client, "CashFlow", {"start_date": "2026-01-01", "end_date": "2026-09-30"}, NOW))
    for inc, begin, end in zip(cf.group("CashIncrease"), cf.group("BeginningCash"), cf.group("EndingCash"), strict=True):
        assert begin + inc == end
    banks = [a for a in bs.accounts.values() if a.path[-1] == "BankAccounts"]
    assert [sum(a.values[i] for a in banks) for i in range(1, 10)] == list(cf.group("EndingCash"))


def test_as_of_hides_entries_posted_later_and_accounts_created_later():
    src = SyntheticQuickBooks()
    northvane = _client(next(p for p in PROFILES if p.id == "northvane-mechanical"))
    params = {"start_date": "2026-08-01", "end_date": "2026-08-31"}
    then = parse(src.report(None, northvane, "CashFlow", params, AUG)).group("EndingCash")[0]
    now = parse(src.report(None, northvane, "CashFlow", params, NOW)).group("EndingCash")[0]
    assert then - now == Decimal("1240.00")
    birchline = _client(next(p for p in PROFILES if p.id == "birchline-grounds"))
    ids_then = {a["Id"] for a in src.accounts(None, birchline, AUG)["QueryResponse"]["Account"]}
    ids_now = {a["Id"] for a in src.accounts(None, birchline, NOW)["QueryResponse"]["Account"]}
    assert ids_now - ids_then == {"120"}


def test_entries_balance_and_amounts_are_whole_cents():
    for company in world().values():
        for e in company.entries:
            assert sum(v for _, v in e.lines) == 0
            assert all(isinstance(v, int) for _, v in e.lines)


def _statement(profile_id: str):
    p = next(p for p in PROFILES if p.id == profile_id)
    src = SyntheticQuickBooks()
    client = _client(p)
    accounts = {a["Id"]: {"account_id": a["Id"], "name": a["Name"], "account_type": a["AccountType"],
                          "account_subtype": a["AccountSubType"], "classification": a["Classification"]}
                for a in src.accounts(None, client, NOW)["QueryResponse"]["Account"]}
    rules = mapping.RulesSuggester()
    mapped = {aid: rules.suggest(a, p.industry).line_key for aid, a in accounts.items()
              if a["classification"] in {"Asset", "Liability", "Equity"}}
    bs = parse(src.report(None, client, "BalanceSheet", {"start_date": "2025-12-01", "end_date": "2026-09-30"}, NOW))
    pl = parse(src.report(None, client, "ProfitAndLoss", {"start_date": "2026-01-01", "end_date": "2026-09-30"}, NOW))
    return statement.build(bs, pl, accounts, mapped)


@pytest.mark.parametrize("client_id", ["wrenfield-dental", "tamsin-creek-brewing", "quillmark-studio", "birchline-grounds"])
def test_indirect_method_identity_holds_for_clean_books(client_id):
    st = _statement(client_id)
    change = statement.net_change(st)
    previous = [st.cash_begin] + st.cash_end[:-1]
    assert [b + c for b, c in zip(previous, change, strict=True)] == st.cash_end
    assert st.equity_change == st.net_income
    assert all(v > 0 for v in st.lines["depreciation"])  # accumulated depreciation growth is an add-back


def test_direct_retained_earnings_posting_breaks_the_identity_by_exactly_that_amount():
    st = _statement("saltbrook-animal-clinic")
    sep = st.month_ends.index(date(2026, 9, 30))
    assert st.net_income[sep] - st.equity_change[sep] == Decimal("2350.00")
    previous = st.cash_end[sep - 1]
    assert previous + statement.net_change(st)[sep] - st.cash_end[sep] == Decimal("2350.00")
