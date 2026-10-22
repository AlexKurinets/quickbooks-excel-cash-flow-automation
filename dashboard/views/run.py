"""Run detail: the checks, the tie-out month by month, and the workbook as a phone preview shows it."""

import streamlit as st

import common as c

clients = c.get("/clients")
names = {cl["id"]: cl["name"] for cl in clients}
run_id = st.query_params.get("run")
period = c.period()

if not run_id:
    client_id = st.selectbox("Client", list(names), format_func=names.get)
    history = [r for r in c.get("/runs", client_id=client_id) if r["period"] == period] or c.get("/runs",
                                                                                                 client_id=client_id)
    if not history:
        st.info("This client has no runs yet.")
        st.stop()
    run_id = history[0]["id"]

run = c.get(f"/runs/{run_id}")
checks = {k["key"]: k for k in run["checks"]}
figures = run["figures"] or {}

st.title(run["client_name"])
template_note = (f' · template {c.esc(run["template_name"])} ({run["formula_count"]} formulas)'
                 if run["formula_count"] else "")
meta = (f'{c.period_label(run["period"])} close · run #{run["id"]} · {c.chip(run["status"])}'
        f'<br><span class="muted">QuickBooks data as of {c.esc(c.when(run["as_of"]))} · started '
        f'{c.esc(c.when(run["started_at"]))} by {c.esc(c.who(run["created_by"]))} · {(run["duration_ms"] or 0) / 1000:.1f} s'
        f'{template_note}'
        f'</span>')
c.html_block(meta)

warnings = [k for k in run["checks"] if k["status"] == "warn"]
if run["status"] in ("held", "failed"):
    c.html_block(f'<div class="banner held"><b>Held.</b> {c.esc(run["reason"])}</div>')
elif warnings:
    c.html_block(f'<div class="banner warn"><b>Ready, with a warning.</b> {c.esc(warnings[0]["summary"])}</div>')
elif run["delivered_at"]:
    c.html_block(f'<div class="banner ready"><b>Sent</b> {c.esc(c.when(run["delivered_at"]))} by '
                 f'{c.esc(c.who(run["delivered_by"]))}.</div>')
else:
    c.html_block('<div class="banner ready"><b>Ready.</b> Every check passed.</div>')

flash = st.session_state.pop("flash", None)
if flash:
    st.success(flash)

actions = []
if run["workbook_bytes"]:
    actions.append("download")
if run["status"] == "ready" and not run["delivered_at"]:
    actions.append("deliver")
if checks.get("qbo_connection", {}).get("status") == "fail":
    actions.append("reconnect")
if checks.get("mapping_complete", {}).get("status") == "fail":
    actions.append("mapping")
actions.append("rerun")
WIDTH = {"download": 1.7, "deliver": 1.3, "reconnect": 2.0, "mapping": 1.9, "rerun": 1.2}
cols = st.columns([WIDTH[a] for a in actions] + [max(1.0, 10 - sum(WIDTH[a] for a in actions))])
for col, action in zip(cols, actions, strict=False):
    if action == "download":
        filename = f'{run["client_name"]} - Cash Flow - {run["period"]}.xlsx'.replace("&", "and")
        col.download_button("Download workbook", c.get_bytes(f"/runs/{run['id']}/workbook"), file_name=filename,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    elif action == "deliver" and col.button("Mark as sent"):
        c.post(f"/runs/{run['id']}/deliver", {"actor": c.actor()})
        st.session_state.flash = "Marked as sent."
        st.rerun()
    elif action == "reconnect" and col.button("Reconnect QuickBooks", type="primary"):
        c.post(f"/clients/{run['client_id']}/reconnect", {"actor": c.actor()})
        new = c.post("/runs", {"client_id": run["client_id"], "period": run["period"], "actor": c.actor()})
        st.session_state.flash = f"Reconnected. Run #{new['id']} finished: {new['status']}."
        st.query_params["run"] = str(new["id"])
        st.rerun()
    elif action == "mapping":
        col.markdown('<a href="mapping" target="_self" style="line-height:2.4">Open mapping review</a>',
                     unsafe_allow_html=True)
    elif action == "rerun" and col.button("Run again"):
        new = c.post("/runs", {"client_id": run["client_id"], "period": run["period"], "actor": c.actor()})
        st.session_state.flash = f"Run #{new['id']} finished: {new['status']}."
        st.query_params["run"] = str(new["id"])
        st.rerun()

st.subheader("Checks")
items = []
for k in run["checks"]:
    icon, color = c.CHECK_ICON[k["status"]]
    tag = "" if k["blocking"] else ' <span class="muted">(warning only)</span>'
    items.append(f'<div class="check"><div class="icon" style="background:{color}">{icon}</div><div>'
                 f'<div class="t">{c.esc(k["title"])}{tag}</div><div class="s">{c.esc(k["summary"])}</div></div></div>')
c.html_block("".join(items))

ties = checks.get("cash_flow_report_ties", {}).get("detail") or {}
cash = checks.get("ending_cash_ties", {}).get("detail") or {}
if ties.get("months") and cash.get("months"):
    st.subheader("Tie-out by month")
    st.caption("Ending cash after the workbook's own formulas are recalculated, against two independent "
               "QuickBooks figures: the bank accounts on the balance sheet and QuickBooks' cash flow report.")
    rows, classes = [], []
    for bs_row, cf_row in zip(cash["months"], ties["months"], strict=True):
        wb_end = bs_row["ending_cash"]["workbook"]
        bs_end = bs_row["ending_cash"]["quickbooks"]
        cf_end = cf_row["ending_cash"]["quickbooks"]
        off = max(abs(bs_row["ending_cash"]["difference"]), abs(cf_row["ending_cash"]["difference"]),
                  abs(cf_row["net_change"]["difference"]))
        diff_cell = c.money(bs_row["ending_cash"]["difference"]) if off >= 0.005 else "–"
        rows.append([bs_row["month"], c.money(cf_row["net_change"]["workbook"]), c.money(wb_end), c.money(bs_end),
                     c.money(cf_end), diff_cell])
        classes.append("")
    headers = [("Month", ""), ("Net change (workbook)", "num"), ("Ending cash (workbook)", "num"),
               ("Balance sheet bank accounts", "num"), ("QuickBooks cash flow report", "num"),
               ("Difference", "num off" if any(r[5] != "–" for r in rows) else "num")]
    c.html_block(c.table(headers, rows, classes))

if run["workbook_bytes"]:
    st.subheader("The workbook, as a phone preview shows it")
    st.caption("Read straight from the finished file's cached values, the way a mail preview or a viewer that "
               "doesn't recalculate would show it. Excel recalculates everything again when the file is opened.")
    pv = c.get(f"/runs/{run['id']}/preview")
    months = len(figures.get("labels", []))
    cols_idx = list(range(months)) + [12]
    headers = [("", "")] + [(pv["columns"][i], "num") for i in cols_idx]
    rows, classes = [], []
    for r in pv["rows"]:
        values = [r["values"][i] for i in cols_idx]
        is_section = r["label"].startswith("Cash flows from")
        if all(v in (None, "") for v in values) and not is_section:
            continue
        rows.append([c.esc(r["label"])] + [c.money(v) if isinstance(v, int | float) else c.esc(v or "") for v in values])
        classes.append("section" if is_section else ("bold" if r["bold"] else ""))
    c.html_block(c.table(headers, rows, classes, wide=True))

st.subheader("Run history")
hist = c.get("/runs", client_id=run["client_id"])
rows = [[f'<a href="run?run={h["id"]}" target="_self">#{h["id"]}</a>', c.esc(c.period_label(h["period"])),
         c.chip(h["status"]), c.esc(c.when(h["started_at"])), c.esc(c.who(h["created_by"])),
         c.esc(c.when(h["delivered_at"])) or "–"] for h in hist]
c.html_block(c.table([("Run", ""), ("Month", ""), ("Status", ""), ("Started", ""), ("By", ""), ("Sent", "")], rows))
