"""One run: pull a client's books for a closed month, fill the workbook, prove it, store the result.

A run ends `ready` only when every blocking check passes, and `held` otherwise, with the first
failing check's plain-language summary as the reason. Warnings do not hold a run. Checks, in order:

  qbo_connection             the client's QuickBooks authorization still works
  template_map               every label the map names is in the template once, inputs are not formulas
  mapping_complete           every balance sheet account with a balance has an approved line
  equity_rollforward         P&L net income equals the change in retained earnings plus current-year profit
  formulas_preserved         formula inventory, untouched parts and untouched cells match the template
  ending_cash_ties           recalculated ending cash equals the balance sheet's bank accounts, every month
  cash_flow_report_ties      recalculated net change and ending cash equal QuickBooks' cash flow report
  template_difference_zero   the template's own Difference row recalculates to zero
  delivered_months_unchanged (warning) months already sent to the client have not changed in QuickBooks
"""

from __future__ import annotations

import calendar
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import db, mapping, statement
from .config import settings
from .qbo.reports import parse
from .qbo.source import ConnectionExpired, QuickBooksSource, make_source
from .workbook import TemplateError, TemplateMap, fill, locate

CENT = Decimal("0.005")
MONTH_NAMES = [calendar.month_abbr[i] for i in range(1, 13)]
FULL = {calendar.month_abbr[i]: calendar.month_name[i] for i in range(1, 13)}
TITLES = {
    "qbo_connection": "QuickBooks connection",
    "template_map": "Template matches its map",
    "mapping_complete": "Every account is mapped",
    "equity_rollforward": "Net income agrees with the change in equity",
    "formulas_preserved": "Template formulas and layout unchanged",
    "ending_cash_ties": "Ending cash ties to the balance sheet",
    "cash_flow_report_ties": "Ties to QuickBooks' cash flow report",
    "template_difference_zero": "Template's own Difference row is zero",
    "delivered_months_unchanged": "Months already sent haven't changed",
}
ORDER = list(TITLES)


def money(value) -> str:
    d = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"-${-d:,.2f}" if d < 0 else f"${d:,.2f}"


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _r(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


@dataclass
class Check:
    key: str
    status: str
    summary: str
    detail: dict | None = None
    blocking: bool = True


@dataclass
class Outcome:
    checks: list[Check] = field(default_factory=list)
    figures: dict = field(default_factory=dict)
    workbook: bytes | None = None
    formula_count: int | None = None

    def add(self, key: str, status: str, summary: str, detail: dict | None = None, blocking: bool = True) -> None:
        self.checks.append(Check(key, status, summary, detail, blocking))

    def skip_rest(self, why: str) -> None:
        done = {c.key for c in self.checks}
        for key in ORDER:
            if key not in done:
                self.add(key, "skipped", why, blocking=key != "delivered_months_unchanged")

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail" and c.blocking]


def _month_label(d: date) -> str:
    return d.strftime("%B %Y")


def _day(d: datetime | date) -> str:
    return f"{d.day} {d:%b %Y}"


def _accounts(payload: dict) -> dict[str, dict]:
    return {a["Id"]: {"account_id": a["Id"], "name": a.get("FullyQualifiedName") or a["Name"],
                      "account_type": a["AccountType"], "account_subtype": a.get("AccountSubType", ""),
                      "classification": a.get("Classification", ""), "active": a.get("Active", True)}
            for a in payload.get("QueryResponse", {}).get("Account", [])}


def _snapshot(conn: psycopg.Connection, client_id: str, accounts: dict[str, dict], at: datetime) -> None:
    for a in accounts.values():
        conn.execute(
            """insert into qbo_accounts (client_id, account_id, name, account_type, account_subtype, classification,
                   active, first_seen_at, last_seen_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (client_id, account_id) do update set name = excluded.name,
                   account_type = excluded.account_type, account_subtype = excluded.account_subtype,
                   classification = excluded.classification, active = excluded.active,
                   last_seen_at = excluded.last_seen_at""",
            (client_id, a["account_id"], a["name"], a["account_type"], a["account_subtype"], a["classification"],
             a["active"], at, at))


def _evaluate(conn, client: dict, period: date, as_of: datetime, run_id: int, source: QuickBooksSource, suggester,
              template: bytes, tmap: TemplateMap, now: datetime) -> Outcome:
    out = Outcome()
    try:
        source.check_connection(conn, client)
        out.add("qbo_connection", "pass", f"Connected to QuickBooks company {client['realm_id']}.")
    except ConnectionExpired as exc:
        when = f"on {_day(exc.expired_at)}" if exc.expired_at else "recently"
        out.add("qbo_connection", "fail", f"The QuickBooks connection for this client expired {when}. Someone with "
                "admin access to the client's QuickBooks needs to reconnect it before the workbook can be built.")
        out.skip_rest("Not run: no QuickBooks connection.")
        return out

    try:
        layout = locate(template, tmap)
        out.add("template_map", "pass", f"All {len(tmap.inputs) + len(tmap.outputs) + len(tmap.fields)} labels "
                "the map names were found once each, and no input cell holds a formula.")
    except TemplateError as exc:
        out.add("template_map", "fail", f"The template doesn't match its map: {exc}.")
        out.skip_rest("Not run: the template has to be fixed first.")
        return out

    fy_start = date(period.year, client["fiscal_year_start"], 1)
    before = date(fy_start.year - 1, 12, 1) if fy_start.month == 1 else date(fy_start.year, fy_start.month - 1, 1)
    accounts = _accounts(source.accounts(conn, client, as_of))
    _snapshot(conn, client["id"], accounts, now)
    bs = parse(source.report(conn, client, "BalanceSheet",
                             {"start_date": before.isoformat(), "end_date": period.isoformat()}, as_of))
    pl = parse(source.report(conn, client, "ProfitAndLoss",
                             {"start_date": fy_start.isoformat(), "end_date": period.isoformat()}, as_of))
    cf = parse(source.report(conn, client, "CashFlow",
                             {"start_date": fy_start.isoformat(), "end_date": period.isoformat()}, as_of))

    st = statement.build(bs, pl, accounts, mapping.approved(conn, client["id"]))
    ends = st.month_ends
    labels = [MONTH_NAMES[e.month - 1] for e in ends]
    out.figures = {"months": [e.isoformat() for e in ends], "labels": labels,
                   "qbo": {"bs_cash": [_r(v) for v in st.cash_end], "pl_net_income": [_r(v) for v in st.net_income],
                           "equity_change": [_r(v) for v in st.equity_change],
                           "cf_net_change": [_r(v) for v in cf.group("CashIncrease")],
                           "cf_ending_cash": [_r(v) for v in cf.group("EndingCash")]},
                   "cash_begin": _r(st.cash_begin)}

    if st.unmapped:
        names = []
        for a in st.unmapped:
            mapping.file_suggestion(conn, client, a, suggester, run_id, now)
            names.append(f"{a['name']} ({a['account_type']}, {a['account_subtype']})")
        count = f"{len(names)} account has" if len(names) == 1 else f"{len(names)} accounts have"
        out.add("mapping_complete", "fail", f"{count} no approved line yet: {'; '.join(names)}. A suggested line is "
                "waiting in the mapping queue, and the run can go again once someone approves it.",
                {"unmapped": st.unmapped})
        out.skip_rest("Not run: waiting on mapping approval.")
        return out
    mapped = len(bs.accounts)
    out.add("mapping_complete", "pass", f"All {mapped} balance sheet accounts with a balance have an approved line.")

    gaps = [(labels[i], st.equity_change[i] - st.net_income[i]) for i in range(len(ends))
            if abs(st.equity_change[i] - st.net_income[i]) >= CENT]
    if gaps:
        month, gap = gaps[0]
        out.add("equity_rollforward", "fail", f"In {FULL[month]} equity moved {money(abs(gap))} "
                f"{'less' if gap < 0 else 'more'} than net income, which usually means a journal entry posted "
                "directly to retained earnings. The cash flow statement can't place that amount; ask the bookkeeper "
                "to reclassify it.", {"months": [{"month": m, "gap": _r(g)} for m, g in gaps]})
    else:
        out.add("equity_rollforward", "pass", f"P&L net income equals the change in equity in all {len(ends)} months.")

    inputs: dict[str, object] = {
        layout.fields["client_name"]: client["name"],
        layout.fields["fiscal_year"]: fy_start.year,
        layout.fields["closed_through"]: _month_label(period),
        layout.fields["source"]: (f"QuickBooks Online company {client['realm_id']}, data as of "
                                  f"{as_of.astimezone(UTC):%Y-%m-%d %H:%M} UTC, run {run_id}"),
        layout.cell("cash_beginning", tmap.months[0]): st.cash_begin,
    }
    for i, month in enumerate(labels):
        inputs[layout.cell("net_income", month)] = st.net_income[i]
        inputs[layout.cell("cash_per_balance_sheet", month)] = st.cash_end[i]
        for key, values in st.lines.items():
            inputs[layout.cell(key, month)] = values[i]
    filled = fill(template, layout, inputs)
    out.workbook = filled.workbook
    v = filled.verification
    out.formula_count = v.formula_count
    wb = {key: [_r(filled.value(key, m)) for m in labels] for key in tmap.outputs if key != "difference"}
    wb["difference"] = [filled.value("difference", m) for m in labels]
    wb["net_income"] = [_r(filled.value("net_income", m)) for m in labels]
    out.figures["workbook"] = wb
    out.figures["lines"] = {key: [_r(x) for x in values] for key, values in st.lines.items()}
    out.figures["accounts"] = {aid: {"name": a["name"], "line": a["line"], "effects": [_r(x) for x in a["effects"]]}
                               for aid, a in st.by_account.items()}

    if v.ok:
        out.add("formulas_preserved", "pass", f"All {v.formula_count} formulas are unchanged, and apart from the "
                f"{len(inputs)} input cells and the formulas' cached results, every part of the file matches the "
                f"template byte for byte ({len(v.untouched_parts)} of {len(v.untouched_parts) + len(v.changed_parts)} "
                "package parts untouched, including the chart and the text box).",
                {"untouched_parts": v.untouched_parts, "changed_parts": v.changed_parts})
    else:
        out.add("formulas_preserved", "fail", "The finished file differs from the template outside the input cells: "
                f"{', '.join((v.changed_formulas + v.unexpected_parts + v.unexpected_cells)[:5])}.",
                {"changed_formulas": v.changed_formulas, "unexpected_parts": v.unexpected_parts,
                 "unexpected_cells": v.unexpected_cells})

    def tie(key: str, what: str, pairs: dict[str, tuple[list[float], list[float]]], ok: str) -> None:
        rows, bad = [], []
        for i, month in enumerate(labels):
            row = {"month": month}
            for name, (ours, theirs) in pairs.items():
                gap = _r(ours[i] - theirs[i])
                row[name] = {"workbook": ours[i], "quickbooks": theirs[i], "difference": gap}
                if abs(Decimal(str(gap))) >= CENT:
                    bad.append((month, name, ours[i], theirs[i], gap))
            rows.append(row)
        if bad:
            month, name, ours_v, theirs_v, gap = bad[0]
            months_off = len({b[0] for b in bad})
            out.add(key, "fail", f"{what} doesn't tie in {months_off} of {len(labels)} months. First is {FULL[month]} "
                    f"({name.replace('_', ' ')}): workbook {money(ours_v)}, QuickBooks {money(theirs_v)}, off by "
                    f"{money(abs(gap))}.", {"months": rows})
        else:
            out.add(key, "pass", ok, {"months": rows})

    qbo = out.figures["qbo"]
    tie("ending_cash_ties", "Recalculated ending cash", {"ending_cash": (wb["ending_cash"], qbo["bs_cash"])},
        f"Recalculated ending cash equals the balance sheet's bank accounts in all {len(labels)} months "
        f"({FULL[labels[-1]]}: {money(wb['ending_cash'][-1])}).")
    tie("cash_flow_report_ties", "The workbook against QuickBooks' cash flow report",
        {"net_change": (wb["net_change"], qbo["cf_net_change"]), "ending_cash": (wb["ending_cash"], qbo["cf_ending_cash"])},
        f"Net change and ending cash agree with QuickBooks' own cash flow report in all {len(labels)} months.")

    diffs = [d for d in wb["difference"] if d not in ("", None) and abs(Decimal(str(d))) >= CENT]
    if diffs:
        out.add("template_difference_zero", "fail", f"The template's Difference row is not zero in {len(diffs)} "
                "month(s), so a reviewer opening the file will see red cells.")
    else:
        out.add("template_difference_zero", "pass", f"The template's own Difference row recalculates to zero in all "
                f"{len(labels)} months.")

    _drift(conn, client, period, out, labels)
    return out


def _drift(conn, client: dict, period: date, out: Outcome, labels: list[str]) -> None:
    prior = conn.execute(
        """select id, period, delivered_at, figures from runs where client_id = %s and period < %s
           and delivered_at is not null order by period desc, id desc limit 1""", (client["id"], period)).fetchone()
    if prior is None:
        out.add("delivered_months_unchanged", "pass", "No earlier workbook for this fiscal year has been sent yet.",
                blocking=False)
        return
    then, now_ = prior["figures"], out.figures["qbo"]
    changes = []
    for i, label in enumerate(then["labels"]):
        if i >= len(labels):
            break
        dc = _r(now_["bs_cash"][i] - then["qbo"]["bs_cash"][i])
        dn = _r(now_["pl_net_income"][i] - then["qbo"]["pl_net_income"][i])
        if dc or dn:
            changes.append({"month": label, "cash_change": dc, "net_income_change": dn,
                            "cash_then": then["qbo"]["bs_cash"][i], "cash_now": now_["bs_cash"][i]})
    sent = _day(prior["delivered_at"])
    if not changes:
        out.add("delivered_months_unchanged", "pass", f"Nothing in the {_month_label(prior['period'])} workbook sent "
                f"on {sent} has changed in QuickBooks since.", blocking=False)
        return
    c = changes[0]

    def moved(x: float) -> str:
        return f"{money(abs(x))} {'lower' if x < 0 else 'higher'}"

    out.add("delivered_months_unchanged", "warn",
            f"{FULL[c['month']]} changed in QuickBooks after the {_month_label(prior['period'])} workbook was sent on {sent}: "
            f"ending cash is now {moved(c['cash_change'])} and net income {moved(c['net_income_change'])}. This "
            f"workbook uses the current figures, so the client may need a corrected {FULL[c['month']]} workbook.",
            {"months": changes, "prior_run": prior["id"]}, blocking=False)


def run_client(conn: psycopg.Connection, client_id: str, period: date, *, actor: str = "system",
               as_of: datetime | None = None, now: datetime | None = None, source: QuickBooksSource | None = None,
               suggester=None, template_path: Path | None = None, map_path: Path | None = None) -> int:
    s = settings()
    now = now or datetime.now(UTC)
    as_of = as_of or now
    period = month_end(period)
    source = source or make_source()
    suggester = suggester or mapping.make_suggester()
    template_path = template_path or s.template
    template = Path(template_path).read_bytes()
    tmap = TemplateMap.load(map_path or s.template_map)
    client = conn.execute("select * from clients where id = %s", (client_id,)).fetchone()
    if client is None:
        raise KeyError(f"no client {client_id!r}")
    started = time.monotonic()
    run_id = conn.execute(
        """insert into runs (client_id, period, as_of, status, started_at, created_by, template_name, template_sha256)
           values (%s, %s, %s, 'running', %s, %s, %s, %s) returning id""",
        (client_id, period, as_of, now, actor, Path(template_path).name, hashlib.sha256(template).hexdigest())
    ).fetchone()["id"]
    try:
        out = _evaluate(conn, client, period, as_of, run_id, source, suggester, template, tmap, now)
        status = "held" if out.failed else "ready"
        reason = out.failed[0].summary if out.failed else None
    except Exception as exc:
        out, status, reason = Outcome(), "failed", f"{type(exc).__name__}: {exc}"
    duration = int((time.monotonic() - started) * 1000)
    for i, c in enumerate(out.checks):
        conn.execute("""insert into run_checks (run_id, ordinal, key, title, status, blocking, summary, detail)
                        values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                     (run_id, i, c.key, TITLES[c.key], c.status, c.blocking, c.summary,
                      Jsonb(c.detail) if c.detail is not None else None))
    conn.execute(
        """update runs set status = %s, reason = %s, finished_at = %s, duration_ms = %s, formula_count = %s,
               workbook = %s, workbook_sha256 = %s, figures = %s where id = %s""",
        (status, reason, now, duration, out.formula_count, out.workbook,
         hashlib.sha256(out.workbook).hexdigest() if out.workbook else None, Jsonb(out.figures), run_id))
    db.audit(conn, actor, "run", client_id, {"run": run_id, "period": period.isoformat(), "status": status}, now)
    conn.commit()
    return run_id


def deliver(conn: psycopg.Connection, run_id: int, actor: str, at: datetime | None = None) -> None:
    at = at or datetime.now(UTC)
    row = conn.execute("select status, client_id, delivered_at from runs where id = %s for update", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"no run {run_id}")
    if row["status"] != "ready":
        raise ValueError(f"run {run_id} is {row['status']}; only a ready workbook can be marked as sent")
    if row["delivered_at"] is None:
        conn.execute("update runs set delivered_at = %s, delivered_by = %s where id = %s", (at, actor, run_id))
        db.audit(conn, actor, "run.deliver", row["client_id"], {"run": run_id}, at)


def approve_and_rerun(conn: psycopg.Connection, suggestion_id: int, actor: str, line_key: str | None = None,
                      now: datetime | None = None, **run_kwargs) -> tuple[dict, int | None]:
    """Approve a suggested mapping, then run the held period again once the client has nothing else pending."""
    now = now or datetime.now(UTC)
    decided = mapping.approve(conn, suggestion_id, actor, now, line_key)
    conn.commit()
    held = conn.execute("select period from runs where id = %s", (decided["run_id"],)).fetchone()
    pending = conn.execute("select count(*) as n from mapping_suggestions where client_id = %s and status = 'pending'",
                           (decided["client_id"],)).fetchone()["n"]
    if held is None or pending:
        return decided, None
    return decided, run_client(conn, decided["client_id"], held["period"], actor=actor, now=now, **run_kwargs)


def reconnect(conn: psycopg.Connection, client_id: str, actor: str, at: datetime | None = None) -> None:
    """Synthetic stand-in for an admin finishing Intuit's OAuth consent screen again."""
    at = at or datetime.now(UTC)
    conn.execute("update clients set connection_status = 'connected', connection_expired_at = null where id = %s",
                 (client_id,))
    db.audit(conn, actor, "connection.reconnect", client_id, {}, at)
    conn.commit()
