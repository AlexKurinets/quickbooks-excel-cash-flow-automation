"""Close status: every client's latest run for the month, and what each held one needs."""

import streamlit as st

import common as c

period = c.period()
if period is None:
    st.info("No runs yet. Run `cashflow seed` or `cashflow run --all --period 2026-09`.")
    st.stop()
data = c.get("/close", period=period)
counts = data["counts"]

st.title(f"{c.period_label(period)} close")
st.caption("Each client's latest run for the month. A workbook is ready only when every check passes; "
           "a held one says in plain words what it is waiting for.")
c.html_block(c.tiles([
    (str(counts["ready"]), "ready to send", "#1f7a4d"),
    (str(counts["held"]), "held", "#a3361c"),
    (str(counts["sent"]), "sent to the client", "#1f5f8b"),
    (str(data["pending_mappings"]), "mapping suggestions waiting", "#9a6a00"),
]))

rows = []
for r in data["clients"]:
    if r["run_id"]:
        name = f'<a href="run?run={r["run_id"]}" target="_self">{c.esc(r["name"])}</a>'
    else:
        name = c.esc(r["name"])
    if r["status"] == "held" or r["status"] == "failed":
        needs = c.esc(r["reason"])
    elif r["warning"]:
        needs = f'<span style="color:#9a6a00;font-weight:600">Warning.</span> {c.esc(r["warning"])}'
    elif r["delivered_at"]:
        needs = f'Sent {c.esc(c.when(r["delivered_at"]))}'
    elif r["status"] == "ready":
        needs = "Ready to review and send."
    else:
        needs = "Not run for this month."
    run = f'#{r["run_id"]} · {(r["duration_ms"] or 0) / 1000:.1f} s' if r["run_id"] else ""
    rows.append([name, c.esc(r["industry"]), c.chip(r["status"]), needs, f'<span class="muted">{run}</span>'])
c.html_block(c.table([("Client", ""), ("Industry", ""), ("Status", ""), ("What it needs", ""), ("Run", "num")], rows))
