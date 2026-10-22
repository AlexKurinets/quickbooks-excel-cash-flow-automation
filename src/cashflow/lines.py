"""The cash flow lines a balance sheet account can be mapped to.

The keys are what mappings store; the template map ties each key to a row label in the workbook.
The descriptions are what a reviewer sees in the mapping queue and what the suggestion model reads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    key: str
    section: str        # operating, investing, financing, cash, excluded
    title: str
    description: str


LINES = [
    Line("depreciation", "operating", "Depreciation and amortization",
         "Accumulated depreciation or amortization; the non-cash expense is added back to net income."),
    Line("accounts_receivable", "operating", "Accounts receivable",
         "Amounts customers owe; an increase uses cash."),
    Line("inventory", "operating", "Inventory", "Goods held for sale or for use in jobs."),
    Line("prepaid_other_assets", "operating", "Prepaid expenses and other current assets",
         "Prepaid insurance, deposits paid on current purchases, employee advances, other short-term assets."),
    Line("accounts_payable", "operating", "Accounts payable", "Bills owed to vendors."),
    Line("credit_cards", "operating", "Credit cards payable", "Balances on business credit cards."),
    Line("payroll_liabilities", "operating", "Accrued payroll and payroll taxes",
         "Wages, withholding and payroll taxes owed but not yet paid."),
    Line("sales_tax", "operating", "Sales tax payable", "Sales tax collected from customers and owed to the state."),
    Line("deferred_revenue", "operating", "Deferred revenue",
         "Customer payments received before the work is done: retainers, deposits, unearned revenue."),
    Line("other_current_liabilities", "operating", "Other current liabilities",
         "Accrued expenses and other short-term obligations that are part of operations."),
    Line("property_equipment", "investing", "Purchases of property and equipment",
         "Vehicles, machinery, equipment, furniture, leasehold improvements at cost."),
    Line("other_investing", "investing", "Other investing activities",
         "Long-term deposits, notes receivable, investments and other long-term assets."),
    Line("loans", "financing", "Loans", "Term loans, equipment loans, notes payable and similar borrowing."),
    Line("line_of_credit", "financing", "Line of credit", "Revolving bank lines of credit."),
    Line("owner_contributions", "financing", "Owner contributions", "Capital put in by owners, partners or members."),
    Line("owner_distributions", "financing", "Owner distributions",
         "Draws and distributions paid to owners, partners or members."),
    Line("cash", "cash", "Cash and cash equivalents", "Bank, savings and money market accounts."),
    Line("equity_closing", "excluded", "Retained earnings",
         "Retained earnings; it moves with net income, so it is not a cash flow line."),
]
BY_KEY = {line.key: line for line in LINES}
FLOW_KEYS = [line.key for line in LINES if line.section in {"operating", "investing", "financing"}]
