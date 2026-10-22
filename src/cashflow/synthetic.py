"""The made-up client companies and their double-entry books.

Eight small businesses, each with its own chart of accounts and 21 months of journal entries
(January 2025 to September 2026), posted the way a bookkeeper would post them: day-to-day
transactions a day after they happen, month-end adjustments a few days into the next month.
Every entry carries a posting timestamp, so the synthetic QuickBooks source can answer "what did
the books say on 8 September" as well as "what do they say now".

Four clients carry one event each that the September close has to deal with:

* Birchline Grounds opens a new equipment loan account in September (an unmapped account).
* Northvane Mechanical posts an August debit card purchase on 24 September, after the August
  workbook went out (a delivered month that changed).
* Saltbrook Animal Clinic books a prior-year supplier invoice straight to retained earnings
  (equity that moves outside net income).
* Rye & Ember Bakery's QuickBooks connection expires on 2 October (seeded in `seed.py`).

All amounts are integer cents. Every company, person and figure here is invented.
"""

from __future__ import annotations

import calendar
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache

FIRST_MONTH = date(2025, 1, 1)
LAST_MONTH = date(2026, 9, 1)


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    type: str
    subtype: str
    classification: str
    created: datetime = datetime(2024, 11, 4, 15, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Entry:
    date: date
    posted_at: datetime
    memo: str
    lines: tuple[tuple[str, int], ...]  # (account id, amount in cents, debit positive)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    industry: str
    realm_id: str
    revenue: int                 # base monthly revenue, dollars
    season: tuple[float, ...]    # 12 monthly multipliers
    invoiced: float              # share of revenue billed on account
    collect: float               # share of opening A/R collected each month
    taxable: float               # share of point-of-sale revenue subject to 7% sales tax
    cogs: float
    inventory: bool
    payroll: int                 # monthly gross wages, dollars
    payroll_seasonal: bool
    rent: int
    card: int                    # monthly card spend
    insurance: int               # annual premium, paid each January
    fixed: dict[str, int]        # account id -> original cost
    dep_months: int
    loan: tuple[int, float, int] | None  # opening balance, annual rate, monthly payment
    loc_limit: int
    retainers: int               # billed in advance each month, recognized as earned
    distributions: int
    opening_cash: int
    savings: int


CHART = [
    Account("35", "Operating Checking", "Bank", "Checking", "Asset"),
    Account("36", "Reserve Savings", "Bank", "Savings", "Asset"),
    Account("84", "Accounts Receivable (A/R)", "Accounts Receivable", "AccountsReceivable", "Asset"),
    Account("81", "Inventory Asset", "Other Current Asset", "Inventory", "Asset"),
    Account("82", "Prepaid Expenses", "Other Current Asset", "PrepaidExpenses", "Asset"),
    Account("37", "Vehicles", "Fixed Asset", "Vehicles", "Asset"),
    Account("38", "Machinery & Equipment", "Fixed Asset", "MachineryAndEquipment", "Asset"),
    Account("39", "Furniture & Fixtures", "Fixed Asset", "FurnitureAndFixtures", "Asset"),
    Account("40", "Accumulated Depreciation", "Fixed Asset", "AccumulatedDepreciation", "Asset"),
    Account("33", "Accounts Payable (A/P)", "Accounts Payable", "AccountsPayable", "Liability"),
    Account("43", "Business Credit Card", "Credit Card", "CreditCard", "Liability"),
    Account("44", "Payroll Liabilities", "Other Current Liability", "PayrollTaxPayable", "Liability"),
    Account("45", "Sales Tax Payable", "Other Current Liability", "SalesTaxPayable", "Liability"),
    Account("46", "Deferred Revenue - Retainers", "Other Current Liability", "OtherCurrentLiabilities", "Liability"),
    Account("47", "Line of Credit", "Other Current Liability", "LineOfCredit", "Liability"),
    Account("48", "Equipment Loan", "Long Term Liability", "NotesPayable", "Liability"),
    Account("50", "Member Contributions", "Equity", "PartnerContributions", "Equity"),
    Account("51", "Member Distributions", "Equity", "PartnerDistributions", "Equity"),
    Account("52", "Retained Earnings", "Equity", "RetainedEarnings", "Equity"),
    Account("91", "Sales", "Income", "SalesOfProductIncome", "Revenue"),
    Account("92", "Service Revenue", "Income", "ServiceFeeIncome", "Revenue"),
    Account("95", "Cost of Goods Sold", "Cost of Goods Sold", "SuppliesMaterialsCogs", "Expense"),
    Account("101", "Wages and Salaries", "Expense", "PayrollExpenses", "Expense"),
    Account("102", "Rent", "Expense", "RentOrLeaseOfBuildings", "Expense"),
    Account("103", "Insurance", "Expense", "Insurance", "Expense"),
    Account("104", "Supplies", "Expense", "SuppliesMaterials", "Expense"),
    Account("105", "Advertising", "Expense", "AdvertisingPromotional", "Expense"),
    Account("106", "Utilities", "Expense", "Utilities", "Expense"),
    Account("107", "Interest Expense", "Expense", "InterestPaid", "Expense"),
    Account("108", "Depreciation", "Other Expense", "Depreciation", "Expense"),
]
NEW_LOAN = Account("120", "Equipment Loan - Mower Fleet", "Long Term Liability", "NotesPayable", "Liability",
                   created=datetime(2026, 9, 10, 14, 20, tzinfo=UTC))

FLAT = (1.0,) * 12
PROFILES = [
    Profile("birchline-grounds", "Birchline Grounds LLC", "Landscaping", "4620816365000101", 95_000,
            (0.35, 0.40, 0.80, 1.20, 1.40, 1.45, 1.40, 1.35, 1.25, 1.00, 0.60, 0.40), 0.85, 0.65, 0.0, 0.22, False,
            38_000, True, 4_200, 6_500, 18_000, {"37": 140_000, "38": 210_000}, 84, (120_000, 0.069, 3_100), 60_000, 0, 9_000, 85_000, 20_000),
    Profile("wrenfield-dental", "Wrenfield Dental Studio PLLC", "Dental practice", "4620816365000102", 140_000,
            (0.95, 0.95, 1.02, 1.00, 1.00, 0.97, 0.92, 1.00, 1.03, 1.05, 1.00, 1.06), 0.55, 0.70, 0.0, 0.07, False,
            62_000, False, 9_800, 7_000, 14_400, {"38": 260_000, "39": 45_000}, 84, (300_000, 0.072, 6_200), 0, 0, 30_000, 120_000, 40_000),
    Profile("tamsin-creek-brewing", "Tamsin Creek Brewing Co.", "Craft brewery", "4620816365000103", 88_000,
            (0.70, 0.72, 0.85, 0.95, 1.10, 1.25, 1.35, 1.30, 1.10, 1.00, 0.85, 1.00), 0.40, 0.60, 0.60, 0.34, True,
            30_000, False, 7_500, 5_500, 12_000, {"38": 380_000, "39": 40_000}, 120, (210_000, 0.075, 4_300), 0, 0, 6_000, 60_000, 15_000),
    Profile("ollery-lane-property", "Ollery Lane Property Services", "Property management", "4620816365000104", 52_000,
            FLAT, 0.90, 0.80, 0.0, 0.18, False,
            24_000, False, 3_600, 3_000, 9_600, {"37": 65_000, "39": 18_000}, 60, None, 0, 0, 8_000, 70_000, 30_000),
    Profile("northvane-mechanical", "Northvane Mechanical Inc.", "HVAC contractor", "4620816365000105", 160_000,
            (0.80, 0.75, 0.85, 0.95, 1.10, 1.30, 1.40, 1.35, 1.10, 0.95, 0.85, 0.90), 0.92, 0.55, 0.0, 0.38, True,
            58_000, False, 6_800, 9_000, 21_000, {"37": 260_000, "38": 90_000}, 72, (150_000, 0.070, 3_900), 120_000, 0, 12_000, 95_000, 25_000),
    Profile("quillmark-studio", "Quillmark Studio LLC", "Design agency", "4620816365000106", 64_000,
            (0.90, 0.95, 1.00, 1.00, 1.05, 1.00, 0.90, 0.95, 1.05, 1.10, 1.05, 1.05), 0.75, 0.70, 0.0, 0.12, False,
            36_000, False, 5_200, 4_500, 6_000, {"38": 42_000, "39": 30_000}, 36, None, 0, 18_000, 20_000, 55_000, 10_000),
    Profile("saltbrook-animal-clinic", "Saltbrook Animal Clinic", "Veterinary clinic", "4620816365000107", 118_000,
            FLAT, 0.10, 0.80, 0.15, 0.20, True,
            52_000, False, 8_400, 6_000, 13_200, {"38": 230_000, "39": 35_000}, 96, (180_000, 0.071, 4_000), 0, 0, 14_000, 90_000, 20_000),
    Profile("rye-and-ember-bakery", "Rye & Ember Bakery", "Bakery", "4620816365000108", 72_000,
            (0.90, 0.85, 0.95, 0.95, 1.00, 1.00, 0.95, 0.95, 1.00, 1.05, 1.15, 1.35), 0.20, 0.70, 0.50, 0.32, True,
            26_000, False, 6_200, 4_800, 7_800, {"38": 120_000, "39": 22_000}, 84, (60_000, 0.078, 1_700), 0, 0, 5_000, 40_000, 10_000),
]


@dataclass
class Company:
    profile: Profile
    accounts: dict[str, Account]
    entries: list[Entry] = field(default_factory=list)

    def accounts_as_of(self, as_of: datetime) -> list[Account]:
        return [a for a in self.accounts.values() if a.created <= as_of]

    def entries_as_of(self, as_of: datetime) -> list[Entry]:
        return [e for e in self.entries if e.posted_at <= as_of]


def cents(dollars: float) -> int:
    return int(round(dollars * 100))


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def months(first: date, last: date) -> list[date]:
    out, d = [], first.replace(day=1)
    while d <= last:
        out.append(d)
        d = next_month(d)
    return out


def _posted(d: date, adjusting: bool = False) -> datetime:
    """Day-to-day entries post the next day; month-end adjustments post on the 3rd of next month."""
    if adjusting:
        n = next_month(d)
        return datetime(n.year, n.month, 3, 16, 0, tzinfo=UTC)
    return datetime(d.year, d.month, d.day, 15, 0, tzinfo=UTC) + timedelta(days=1)


class _Book:
    def __init__(self, company: Company) -> None:
        self.company = company
        self.bal: dict[str, int] = defaultdict(int)

    def post(self, d: date, memo: str, lines: list[tuple[str, int]], posted: datetime | None = None,
             adjusting: bool = False) -> None:
        lines = [(a, v) for a, v in lines if v]
        if not lines:
            return
        assert sum(v for _, v in lines) == 0, (memo, lines)
        for a, _ in lines:
            assert a in self.company.accounts, a
        self.company.entries.append(Entry(d, posted or _posted(d, adjusting), memo, tuple(lines)))
        for a, v in lines:
            self.bal[a] += v

    def credit_balance(self, account: str) -> int:
        return -self.bal[account]


def _chart(p: Profile) -> dict[str, Account]:
    used = {"35", "36", "84", "82", "40", "33", "43", "44", "50", "51", "52", "92", "95", "101", "102", "103",
            "104", "105", "106", "108"}
    used |= set(p.fixed)
    if p.inventory:
        used |= {"81", "91"}
    if p.taxable:
        used |= {"45", "91"}
    if p.retainers:
        used.add("46")
    if p.loc_limit:
        used.add("47")
    if p.loan:
        used |= {"48", "107"}
    accounts = {a.id: a for a in CHART if a.id in used}
    if p.id == "birchline-grounds":
        accounts[NEW_LOAN.id] = NEW_LOAN
    return accounts


def _generate(p: Profile) -> Company:
    company = Company(p, _chart(p))
    book = _Book(company)
    rng = random.Random(p.id)
    sales_acct = "91" if (p.inventory or p.taxable) else "92"

    # Opening balances at 31 Dec 2024, as converted into QuickBooks.
    invoiced0 = cents(p.revenue * p.season[11] * p.invoiced)
    purchases0 = cents(p.revenue * p.cogs)
    cost = {a: cents(v) for a, v in p.fixed.items()}
    opening = [("35", cents(p.opening_cash)), ("36", cents(p.savings)), ("84", int(invoiced0 / p.collect)),
               ("33", -int(purchases0 * 1.1)), ("43", -cents(p.card)), ("44", -cents(p.payroll * 0.23)),
               ("40", -int(sum(cost.values()) * 0.35)), ("50", -cents(25_000))]
    opening += list(cost.items())
    if p.inventory:
        opening.append(("81", int(purchases0 * 1.5)))
    if p.taxable:
        opening.append(("45", -cents(p.revenue * (1 - p.invoiced) * p.taxable * 0.07)))
    if p.retainers:
        opening.append(("46", -cents(p.retainers * 0.5)))
    if p.loan:
        opening.append(("48", -cents(p.loan[0])))
    opening.append(("52", -sum(v for _, v in opening)))
    book.post(date(2024, 12, 31), "Opening balances", opening, posted=datetime(2025, 1, 2, 17, 0, tzinfo=UTC))

    monthly_dep = int(sum(cost.values()) / p.dep_months)
    for m in months(FIRST_MONTH, LAST_MONTH):
        idx = m.month - 1
        end = month_end(m)
        growth = 1.05 if m.year == 2026 else 1.0
        start = dict(book.bal)

        def day(n: int, _m: date = m) -> date:
            return _m.replace(day=min(n, calendar.monthrange(_m.year, _m.month)[1]))

        revenue = cents(p.revenue * p.season[idx] * growth * rng.uniform(0.95, 1.05))
        on_account = int(revenue * p.invoiced)
        at_sale = revenue - on_account
        tax = int(at_sale * p.taxable * 0.07)
        lines = [("84", on_account), ("35", at_sale + tax), (sales_acct, -revenue)]
        if tax:
            lines.append(("45", -tax))
        book.post(day(15), "Sales and invoices", lines)

        collected = int(start.get("84", 0) * p.collect)
        book.post(day(10), "Customer payments", [("35", collected), ("84", -collected)])

        cogs = int(revenue * p.cogs)
        if p.inventory:
            bought = int(cogs * rng.uniform(0.92, 1.10))
            book.post(day(8), "Inventory purchases", [("81", bought), ("33", -bought)])
            book.post(end, "Cost of goods sold", [("95", cogs), ("81", -cogs)], adjusting=True)
        else:
            book.post(day(12), "Materials and subcontractors", [("95", cogs), ("33", -cogs)])
        paid = int(-start.get("33", 0) * 0.85)
        book.post(day(20), "Bill payments", [("33", paid), ("35", -paid)])

        season = p.season[idx] if p.payroll_seasonal else 1.0
        wages = cents(p.payroll * season * growth * rng.uniform(0.97, 1.03))
        withheld = int(wages * 0.23)
        book.post(day(25), "Payroll", [("101", wages), ("35", -(wages - withheld)), ("44", -withheld)])
        remitted = -start.get("44", 0)
        book.post(day(14), "Payroll tax deposits", [("44", remitted), ("35", -remitted)])

        book.post(day(1), "Rent", [("102", cents(p.rent)), ("35", -cents(p.rent))])
        spend = cents(p.card * rng.uniform(0.9, 1.1))
        book.post(day(18), "Card purchases", [("104", int(spend * 0.5)), ("105", int(spend * 0.3)),
                                              ("106", spend - int(spend * 0.5) - int(spend * 0.3)), ("43", -spend)])
        card_due = -start.get("43", 0)
        book.post(day(6), "Card payment", [("43", card_due), ("35", -card_due)])
        if p.taxable:
            tax_due = -start.get("45", 0)
            book.post(day(20), "Sales tax payment", [("45", tax_due), ("35", -tax_due)])

        if m.month == 1:
            book.post(day(5), "Annual insurance premium", [("82", cents(p.insurance)), ("35", -cents(p.insurance))])
        book.post(end, "Insurance expense", [("103", cents(p.insurance / 12)), ("82", -cents(p.insurance / 12))],
                  adjusting=True)
        book.post(end, "Depreciation", [("108", monthly_dep), ("40", -monthly_dep)], adjusting=True)

        if p.loan:
            _, rate, payment = p.loan
            interest = int(book.credit_balance("48") * rate / 12)
            book.post(day(5), "Equipment loan payment", [("48", cents(payment) - interest), ("107", interest),
                                                         ("35", -cents(payment))])
        if p.retainers:
            billed = cents(p.retainers * rng.uniform(0.9, 1.1))
            book.post(day(2), "Retainers billed in advance", [("35", billed), ("46", -billed)])
            earned = int(-start.get("46", 0) * 0.6 + billed * 0.4)
            book.post(end, "Retainer revenue earned", [("46", earned), ("92", -earned)], adjusting=True)

        draw = cents(p.distributions)
        if m.month in (1, 4, 6, 9):
            draw += cents(p.distributions * 1.5)
        book.post(day(20), "Member distributions", [("51", draw), ("35", -draw)])

        if p.loc_limit:
            floor, ceiling = cents(p.payroll * 0.8), cents(p.payroll * 2.0)
            cash, owed = book.bal["35"], book.credit_balance("47")
            if cash < floor and owed < cents(p.loc_limit):
                need = -(-(floor - cash) // 500_000) * 500_000
                draw_loc = min(cents(p.loc_limit) - owed, need)
                book.post(day(27), "Line of credit draw", [("35", draw_loc), ("47", -draw_loc)])
            elif cash > ceiling and owed:
                repay = min(owed, (cash - ceiling) // 500_000 * 500_000)
                book.post(day(27), "Line of credit repayment", [("47", repay), ("35", -repay)])

    _events(p, book)
    company.entries.sort(key=lambda e: (e.date, e.posted_at))
    return company


def _events(p: Profile, book: _Book) -> None:
    if p.id == "birchline-grounds":
        book.post(date(2026, 9, 12), "Mower fleet purchase, financed",
                  [("38", cents(56_500)), ("35", -cents(8_500)), ("120", -cents(48_000))],
                  posted=datetime(2026, 9, 12, 16, 5, tzinfo=UTC))
    if p.id == "northvane-mechanical":
        book.post(date(2026, 8, 28), "Debit card purchase found during bank reconciliation",
                  [("104", cents(1_240)), ("35", -cents(1_240))], posted=datetime(2026, 9, 24, 10, 30, tzinfo=UTC))
    if p.id == "saltbrook-animal-clinic":
        book.post(date(2026, 9, 18), "Prior-year supplier invoice entered to retained earnings",
                  [("52", cents(2_350)), ("33", -cents(2_350))], posted=datetime(2026, 9, 19, 11, 0, tzinfo=UTC))


@cache
def world() -> dict[str, Company]:
    return {p.id: _generate(p) for p in PROFILES}


STAFF = [
    ("mara.quist@example.com", "Mara Quist", "Senior accountant, template owner"),
    ("devin.holloway@example.com", "Devin Holloway", "Staff accountant"),
    ("tess.navarro@example.com", "Tess Navarro", "Operations manager"),
]
