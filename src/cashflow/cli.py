"""cashflow: the operator's command line.

  cashflow migrate [--reset]
  cashflow seed
  cashflow close --period 2026-09                 status of every client for a month
  cashflow run --client ID --period 2026-09       run one client (or --all)
  cashflow suggestions                            the mapping queue
  cashflow approve --client ID --by EMAIL [--line KEY]   approve and re-run
  cashflow reject --id N --by EMAIL --note TEXT
  cashflow deliver --client ID --period 2026-09 --by EMAIL
  cashflow simulate reconnect --client ID         synthetic stand-in for re-authorizing QuickBooks
  cashflow export --client ID --period 2026-09 --out FILE.xlsx
  cashflow inventory [TEMPLATE.xlsx]              formula count and fingerprint of a template
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from . import db, mapping, runner, seed
from .config import settings
from .lines import BY_KEY
from .workbook import formula_inventory, sheet_parts


def period_arg(text: str) -> date:
    year, month = (int(x) for x in text.split("-")[:2])
    return date(year, month, calendar.monthrange(year, month)[1])


def _close_rows(conn, period: date) -> list[dict]:
    return conn.execute(
        """select distinct on (c.id) c.id as client_id, c.name, r.id as run_id, r.status, r.reason, r.delivered_at,
                  (select count(*) from run_checks k where k.run_id = r.id and k.status = 'warn') as warnings
           from clients c left join runs r on r.client_id = c.id and r.period = %s
           order by c.id, r.id desc""", (period,)).fetchall()


def cmd_close(args) -> int:
    with db.connect() as conn:
        rows = _close_rows(conn, args.period)
    if args.json:
        print(json.dumps(rows, default=str, indent=2))
        return 0
    for r in rows:
        status = r["status"] or "not run"
        extra = " (sent)" if r["delivered_at"] else (f" ({r['warnings']} warning)" if r["warnings"] else "")
        print(f"{r['client_id']:<26} {status:<8}{extra}  run {r['run_id'] or '-'}")
        if r["reason"]:
            print(f"    {r['reason']}")
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("ready", "held", "failed")}
    print(f"\n{args.period:%B %Y}: {counts['ready']} ready, {counts['held']} held, {counts['failed']} failed, "
          f"{len(rows)} clients")
    return 0


def cmd_run(args) -> int:
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute("select id from clients order by id")] if args.all else [args.client]
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        worst = 0
        for client_id in ids:
            run_id = runner.run_client(conn, client_id, args.period, actor=args.by, as_of=as_of)
            row = conn.execute("select status, reason from runs where id = %s", (run_id,)).fetchone()
            print(json.dumps({"client": client_id, "run": run_id, "status": row["status"], "reason": row["reason"]}))
            worst = max(worst, {"ready": 0, "held": 2, "failed": 3}[row["status"]])
    return worst


def cmd_suggestions(args) -> int:
    with db.connect() as conn:
        rows = conn.execute("""select id, client_id, account_name, account_type, account_subtype, suggested_line,
                                      confidence, provider, reason from mapping_suggestions
                               where status = 'pending' order by id""").fetchall()
    if args.json:
        print(json.dumps(rows, default=str, indent=2))
        return 0
    if not rows:
        print("No suggestions waiting.")
    for r in rows:
        line = BY_KEY[r["suggested_line"]].title if r["suggested_line"] else "no suggestion"
        print(f"#{r['id']} {r['client_id']}: {r['account_name']} ({r['account_type']}, {r['account_subtype']})")
        print(f"    suggested: {line} [{r['suggested_line']}], confidence {r['confidence']}, from {r['provider']}")
        print(f"    {r['reason']}")
    return 0


def cmd_approve(args) -> int:
    with db.connect() as conn:
        if args.id:
            ids = [args.id]
        else:
            ids = [r["id"] for r in conn.execute("select id from mapping_suggestions where client_id = %s "
                                                 "and status = 'pending' order by id", (args.client,))]
        if not ids:
            print("Nothing pending to approve.", file=sys.stderr)
            return 1
        for sid in ids:
            decided, run_id = runner.approve_and_rerun(conn, sid, args.by, args.line)
            out = {"suggestion": sid, "account": decided["account_name"], "line": decided["decided_line"],
                   "rerun": run_id}
            if run_id:
                row = conn.execute("select status, reason from runs where id = %s", (run_id,)).fetchone()
                out.update(status=row["status"], reason=row["reason"])
            print(json.dumps(out))
    return 0


def cmd_reject(args) -> int:
    with db.connect() as conn:
        mapping.reject(conn, args.id, args.by, datetime.now(UTC), args.note)
    print(json.dumps({"suggestion": args.id, "status": "rejected"}))
    return 0


def _latest(conn, client_id: str, period: date) -> dict:
    row = conn.execute("select * from runs where client_id = %s and period = %s order by id desc limit 1",
                       (client_id, period)).fetchone()
    if row is None:
        raise SystemExit(f"no run for {client_id} in {period:%Y-%m}")
    return row


def cmd_deliver(args) -> int:
    with db.connect() as conn:
        run = _latest(conn, args.client, args.period)
        runner.deliver(conn, run["id"], args.by)
    print(json.dumps({"run": run["id"], "delivered": True}))
    return 0


def cmd_simulate(args) -> int:
    with db.connect() as conn:
        runner.reconnect(conn, args.client, "simulate@example.com")
    print(json.dumps({"client": args.client, "connection": "connected"}))
    return 0


def cmd_export(args) -> int:
    with db.connect() as conn:
        run = _latest(conn, args.client, args.period)
    if not run["workbook"]:
        raise SystemExit(f"run {run['id']} is {run['status']} and has no workbook: {run['reason']}")
    Path(args.out).write_bytes(bytes(run["workbook"]))
    print(json.dumps({"run": run["id"], "status": run["status"], "out": args.out, "sha256": run["workbook_sha256"]}))
    return 0


def cmd_inventory(args) -> int:
    import io
    import zipfile

    path = Path(args.template or settings().template)
    data = path.read_bytes()
    print(json.dumps({"template": path.name, "sha256": hashlib.sha256(data).hexdigest(),
                      "formulas": len(formula_inventory(data)),
                      "sheets": list(sheet_parts(zipfile.ZipFile(io.BytesIO(data))))}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cashflow", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("migrate")
    p.add_argument("--reset", action="store_true")
    sub.add_parser("seed")
    p = sub.add_parser("close")
    p.add_argument("--period", type=period_arg, required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("run")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--client")
    g.add_argument("--all", action="store_true")
    p.add_argument("--period", type=period_arg, required=True)
    p.add_argument("--as-of")
    p.add_argument("--by", default="devin.holloway@example.com")
    p = sub.add_parser("suggestions")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", type=int)
    g.add_argument("--client")
    p.add_argument("--line", choices=sorted(BY_KEY))
    p.add_argument("--by", required=True)
    p = sub.add_parser("reject")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)
    p = sub.add_parser("deliver")
    p.add_argument("--client", required=True)
    p.add_argument("--period", type=period_arg, required=True)
    p.add_argument("--by", required=True)
    p = sub.add_parser("simulate")
    p.add_argument("what", choices=["reconnect"])
    p.add_argument("--client", required=True)
    p = sub.add_parser("export")
    p.add_argument("--client", required=True)
    p.add_argument("--period", type=period_arg, required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("inventory")
    p.add_argument("template", nargs="?")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        with db.connect() as conn:
            print(json.dumps({"applied": db.migrate(conn, args.reset)}))
        return 0
    if args.cmd == "seed":
        with db.connect() as conn:
            print(json.dumps(seed.seed(conn), default=str))
        return 0
    return {"close": cmd_close, "run": cmd_run, "suggestions": cmd_suggestions, "approve": cmd_approve,
            "reject": cmd_reject, "deliver": cmd_deliver, "simulate": cmd_simulate, "export": cmd_export,
            "inventory": cmd_inventory}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
