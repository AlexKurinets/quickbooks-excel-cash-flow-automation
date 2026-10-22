"""Account mappings: approved rows, the review queue, and where suggestions come from.

A balance sheet account reaches the workbook only through an approved mapping. When a run meets
an account with none, it files a suggestion and holds the run. The suggestion is a starting point
for the reviewer, never a mapping: nothing is written until a person approves it.

Two suggesters share one interface. `RulesSuggester` works offline from QuickBooks' account type,
detail type and a few name keywords. `OpenAISuggester` asks a model for a line under a JSON schema
whose enum is the list of line keys; any reply that fails validation falls back to the rules, and
the reason says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import psycopg

from . import db
from .lines import BY_KEY, LINES


@dataclass(frozen=True)
class Suggestion:
    line_key: str | None
    confidence: float
    reason: str
    provider: str


SUBTYPE_RULES = {
    "Checking": "cash", "Savings": "cash", "MoneyMarket": "cash", "CashOnHand": "cash",
    "AccountsReceivable": "accounts_receivable",
    "Inventory": "inventory",
    "PrepaidExpenses": "prepaid_other_assets", "OtherCurrentAssets": "prepaid_other_assets",
    "AccumulatedDepreciation": "depreciation", "AccumulatedAmortization": "depreciation",
    "Vehicles": "property_equipment", "MachineryAndEquipment": "property_equipment",
    "FurnitureAndFixtures": "property_equipment", "LeaseholdImprovements": "property_equipment",
    "Buildings": "property_equipment", "Land": "property_equipment",
    "SecurityDeposits": "other_investing", "OtherLongTermAssets": "other_investing",
    "AccountsPayable": "accounts_payable",
    "CreditCard": "credit_cards",
    "PayrollTaxPayable": "payroll_liabilities", "PayrollClearing": "payroll_liabilities",
    "SalesTaxPayable": "sales_tax", "GlobalTaxPayable": "sales_tax",
    "LineOfCredit": "line_of_credit",
    "NotesPayable": "loans", "LoanPayable": "loans", "OtherLongTermLiabilities": "loans",
    "PartnerContributions": "owner_contributions", "OwnersEquity": "owner_contributions",
    "PaidInCapitalOrSurplus": "owner_contributions",
    "PartnerDistributions": "owner_distributions", "PersonalExpense": "owner_distributions",
    "RetainedEarnings": "equity_closing",
}
NAME_RULES = [
    (("deferred", "unearned", "retainer", "customer deposit"), "deferred_revenue"),
    (("line of credit", "loc "), "line_of_credit"),
    (("loan", "note payable"), "loans"),
    (("draw", "distribution"), "owner_distributions"),
]


class RulesSuggester:
    name = "rules"

    def suggest(self, account: dict, industry: str) -> Suggestion:
        name = account["name"].lower()
        subtype = account["account_subtype"]
        detail = f"{account['account_type']} account with detail type {subtype}"
        if subtype in ("OtherCurrentLiabilities", "OtherCurrentAssets", "OtherLongTermLiabilities"):
            for words, key in NAME_RULES:
                if any(w in name for w in words):
                    return Suggestion(key, 0.70, f"{detail}; the name reads as {BY_KEY[key].title.lower()}.", self.name)
        if subtype in SUBTYPE_RULES:
            key = SUBTYPE_RULES[subtype]
            generic = subtype.startswith("Other")
            confidence = 0.55 if generic else 0.90
            return Suggestion(key, confidence, f"{detail}, which normally belongs on {BY_KEY[key].title.lower()}.",
                              self.name)
        if account["account_type"] == "Other Current Liability":
            return Suggestion("other_current_liabilities", 0.40, f"{detail}; nothing more specific matched.", self.name)
        return Suggestion(None, 0.0, f"{detail}; no rule matched, so this needs a person to place it.", self.name)


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "line_key": {"type": "string", "enum": [line.key for line in LINES]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["line_key", "confidence", "reason"],
    "additionalProperties": False,
}

INSTRUCTIONS = (
    "You place one QuickBooks Online balance sheet account on a line of an indirect-method statement of cash "
    "flows prepared by an accounting firm for a small-business client. Choose exactly one line_key from the list. "
    "Use the account type and detail type first and the name second. Give a confidence between 0 and 1 and one "
    "plain sentence a staff accountant can check. A person reviews every answer before it is used."
)


class OpenAISuggester:
    name = "openai"

    def __init__(self, model: str, client=None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self.client = client
        self.model = model
        self.fallback = RulesSuggester()

    def suggest(self, account: dict, industry: str) -> Suggestion:
        lines = "\n".join(f"- {line.key}: {line.title}. {line.description}" for line in LINES)
        prompt = (f"Client industry: {industry}\nAccount name: {account['name']}\n"
                  f"Account type: {account['account_type']}\nDetail type: {account['account_subtype']}\n"
                  f"Classification: {account['classification']}\n\nLines:\n{lines}")
        try:
            resp = self.client.responses.create(
                model=self.model, instructions=INSTRUCTIONS, input=prompt,
                text={"format": {"type": "json_schema", "name": "cash_flow_line", "schema": REPLY_SCHEMA,
                                 "strict": True}})
            reply = json.loads(resp.output_text)
            key = reply["line_key"]
            if key not in BY_KEY:
                raise ValueError(f"unknown line {key!r}")
            confidence = min(max(float(reply["confidence"]), 0.0), 1.0)
            reason = str(reply["reason"]).strip()[:400]
            if not reason:
                raise ValueError("empty reason")
            return Suggestion(key, round(confidence, 2), reason, f"openai:{self.model}")
        except Exception as exc:  # noqa: BLE001 - any failure means the reviewer gets the rules' answer instead
            fallback = self.fallback.suggest(account, industry)
            return Suggestion(fallback.line_key, fallback.confidence,
                              f"{fallback.reason} (Model reply not used: {type(exc).__name__}: {exc})"[:400],
                              "rules")


def make_suggester(kind: str | None = None):
    from .config import settings

    s = settings()
    kind = kind or s.suggester
    if kind == "openai":
        return OpenAISuggester(s.openai_model)
    return RulesSuggester()


# -- store ----------------------------------------------------------------------------------------

def approved(conn: psycopg.Connection, client_id: str) -> dict[str, str]:
    rows = conn.execute("select account_id, line_key from account_mappings where client_id = %s", (client_id,))
    return {r["account_id"]: r["line_key"] for r in rows}


def file_suggestion(conn: psycopg.Connection, client: dict, account: dict, suggester, run_id: int,
                    at: datetime) -> int:
    existing = conn.execute("select id from mapping_suggestions where client_id = %s and account_id = %s "
                            "and status = 'pending'", (client["id"], account["account_id"])).fetchone()
    if existing:
        return existing["id"]
    s = suggester.suggest(account, client["industry"])
    row = conn.execute(
        """insert into mapping_suggestions (client_id, account_id, account_name, account_type, account_subtype,
               classification, suggested_line, confidence, reason, provider, created_at, run_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (client["id"], account["account_id"], account["name"], account["account_type"], account["account_subtype"],
         account["classification"], s.line_key, s.confidence, s.reason, s.provider, at, run_id)).fetchone()
    return row["id"]


class MappingError(Exception):
    pass


def approve(conn: psycopg.Connection, suggestion_id: int, actor: str, at: datetime, line_key: str | None = None) -> dict:
    row = conn.execute("select * from mapping_suggestions where id = %s for update", (suggestion_id,)).fetchone()
    if row is None:
        raise MappingError(f"no suggestion {suggestion_id}")
    if row["status"] != "pending":
        raise MappingError(f"suggestion {suggestion_id} is already {row['status']}")
    key = line_key or row["suggested_line"]
    if key not in BY_KEY:
        raise MappingError(f"choose a line for {row['account_name']}; {key!r} is not a line")
    source = "suggestion" if key == row["suggested_line"] else "manual"
    conn.execute(
        """insert into account_mappings (client_id, account_id, line_key, source, approved_by, approved_at)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (client_id, account_id) do update set line_key = excluded.line_key,
               source = excluded.source, approved_by = excluded.approved_by, approved_at = excluded.approved_at""",
        (row["client_id"], row["account_id"], key, source, actor, at))
    conn.execute("update mapping_suggestions set status = 'approved', decided_line = %s, decided_by = %s, "
                 "decided_at = %s where id = %s", (key, actor, at, suggestion_id))
    db.audit(conn, actor, "mapping.approve", row["client_id"],
             {"suggestion": suggestion_id, "account_id": row["account_id"], "account": row["account_name"],
              "line": key, "suggested": row["suggested_line"], "provider": row["provider"]}, at)
    return {**row, "decided_line": key, "status": "approved"}


def reject(conn: psycopg.Connection, suggestion_id: int, actor: str, at: datetime, note: str) -> dict:
    row = conn.execute("select * from mapping_suggestions where id = %s for update", (suggestion_id,)).fetchone()
    if row is None or row["status"] != "pending":
        raise MappingError(f"no pending suggestion {suggestion_id}")
    conn.execute("update mapping_suggestions set status = 'rejected', decided_by = %s, decided_at = %s, note = %s "
                 "where id = %s", (actor, at, note, suggestion_id))
    db.audit(conn, actor, "mapping.reject", row["client_id"],
             {"suggestion": suggestion_id, "account": row["account_name"], "note": note}, at)
    return {**row, "status": "rejected"}
