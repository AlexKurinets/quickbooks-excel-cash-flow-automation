"""Suggesters and the approval store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cashflow import mapping

AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
LOAN = {"account_id": "120", "name": "Equipment Loan - Mower Fleet", "account_type": "Long Term Liability",
        "account_subtype": "NotesPayable", "classification": "Liability"}


@pytest.mark.parametrize(("name", "atype", "subtype", "expected", "max_conf"), [
    ("Operating Checking", "Bank", "Checking", "cash", 0.9),
    ("Accumulated Depreciation", "Fixed Asset", "AccumulatedDepreciation", "depreciation", 0.9),
    ("Member Distributions", "Equity", "PartnerDistributions", "owner_distributions", 0.9),
    ("Deferred Revenue - Retainers", "Other Current Liability", "OtherCurrentLiabilities", "deferred_revenue", 0.7),
    ("Accrued Bonuses", "Other Current Liability", "OtherCurrentLiabilities", "other_current_liabilities", 0.4),
    ("Retained Earnings", "Equity", "RetainedEarnings", "equity_closing", 0.9),
])
def test_rules_suggester(name, atype, subtype, expected, max_conf):
    s = mapping.RulesSuggester().suggest({"name": name, "account_type": atype, "account_subtype": subtype}, "Bakery")
    assert s.line_key == expected
    assert s.confidence <= max_conf


def test_rules_suggester_gives_up_honestly():
    s = mapping.RulesSuggester().suggest({"name": "Clearing", "account_type": "Other Asset",
                                          "account_subtype": "Goodwill"}, "Bakery")
    assert s.line_key is None and s.confidence == 0


class FakeResponses:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(output_text=json.dumps(self.reply))


def fake_client(reply):
    return SimpleNamespace(responses=FakeResponses(reply))


def test_openai_suggester_sends_a_strict_schema_and_uses_a_valid_reply():
    client = fake_client({"line_key": "loans", "confidence": 0.93, "reason": "Equipment financing is a loan."})
    s = mapping.OpenAISuggester("gpt-5-mini", client).suggest(LOAN, "Landscaping")
    assert (s.line_key, s.confidence, s.provider) == ("loans", 0.93, "openai:gpt-5-mini")
    call = client.responses.calls[0]
    fmt = call["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert "loans" in fmt["schema"]["properties"]["line_key"]["enum"]
    assert "NotesPayable" in call["input"] and "Landscaping" in call["input"]


@pytest.mark.parametrize("reply", [
    {"line_key": "made_up_line", "confidence": 0.9, "reason": "x"},
    {"line_key": "loans", "confidence": 0.9, "reason": "   "},
    RuntimeError("rate limited"),
])
def test_openai_suggester_falls_back_to_rules_and_says_why(reply):
    s = mapping.OpenAISuggester("gpt-5-mini", fake_client(reply)).suggest(LOAN, "Landscaping")
    assert s.provider == "rules" and s.line_key == "loans"
    assert "Model reply not used" in s.reason


def _client_row(conn):
    conn.execute("insert into clients (id, name, industry, realm_id) values ('c1', 'Example Co', 'Bakery', 'r1')")
    return conn.execute("select * from clients where id = 'c1'").fetchone()


def test_filing_is_idempotent_while_pending_and_approval_writes_mapping_and_audit(conn):
    client = _client_row(conn)
    sid = mapping.file_suggestion(conn, client, LOAN, mapping.RulesSuggester(), None, AT)
    assert mapping.file_suggestion(conn, client, LOAN, mapping.RulesSuggester(), None, AT) == sid
    decided = mapping.approve(conn, sid, "mara.quist@example.com", AT)
    assert decided["decided_line"] == "loans"
    assert mapping.approved(conn, "c1") == {"120": "loans"}
    row = conn.execute("select * from account_mappings where client_id = 'c1'").fetchone()
    assert (row["source"], row["approved_by"]) == ("suggestion", "mara.quist@example.com")
    audit = conn.execute("select * from audit_log where action = 'mapping.approve'").fetchone()
    assert audit["detail"]["line"] == "loans"
    with pytest.raises(mapping.MappingError, match="already approved"):
        mapping.approve(conn, sid, "mara.quist@example.com", AT)


def test_a_reviewer_can_choose_a_different_line_but_not_an_invented_one(conn):
    client = _client_row(conn)
    sid = mapping.file_suggestion(conn, client, LOAN, mapping.RulesSuggester(), None, AT)
    mapping.approve(conn, sid, "mara.quist@example.com", AT, line_key="line_of_credit")
    row = conn.execute("select * from account_mappings where client_id = 'c1'").fetchone()
    assert (row["line_key"], row["source"]) == ("line_of_credit", "manual")
    other = mapping.file_suggestion(conn, client, {**LOAN, "account_id": "121"}, mapping.RulesSuggester(), None, AT)
    with pytest.raises(mapping.MappingError, match="not a line"):
        mapping.approve(conn, other, "mara.quist@example.com", AT, line_key="nope")


def test_reject_needs_a_pending_suggestion(conn):
    client = _client_row(conn)
    sid = mapping.file_suggestion(conn, client, LOAN, mapping.RulesSuggester(), None, AT)
    assert mapping.reject(conn, sid, "mara.quist@example.com", AT, "opened in error")["status"] == "rejected"
    with pytest.raises(mapping.MappingError):
        mapping.reject(conn, sid, "mara.quist@example.com", AT, "again")
    assert mapping.approved(conn, "c1") == {}
