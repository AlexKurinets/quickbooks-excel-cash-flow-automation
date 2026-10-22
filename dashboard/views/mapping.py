"""Mapping review: new accounts wait here with a suggested line until a person decides."""

import streamlit as st

import common as c

lines = c.get("/lines")
titles = {line["key"]: line["title"] for line in lines}
sections = {line["key"]: line["section"] for line in lines}
pending = c.get("/suggestions", status="pending")

st.title("Mapping review")
st.caption("A balance sheet account reaches a workbook only through a mapping a person approved. A new account "
           "lands here with a suggested line, and its client's run waits until someone decides.")

flash = st.session_state.pop("flash", None)
if flash:
    st.success(flash)

if not pending:
    st.info("Nothing waiting for review.")

for s in pending:
    suggested = s["suggested_line"]
    conf = f'{float(s["confidence"]) * 100:.0f}% confidence' if s["confidence"] is not None else "no confidence"
    c.html_block(
        f'<div class="card"><div class="muted">{c.esc(s["client_name"])} · {c.esc(s["industry"])} · first seen '
        f'{c.esc(c.when(s["created_at"]))} in run #{s["run_id"]}</div>'
        f'<div style="font-size:1.15rem;font-weight:650;margin:4px 0">{c.esc(s["account_name"])}</div>'
        f'<div class="muted">QuickBooks type {c.esc(s["account_type"])}, detail type {c.esc(s["account_subtype"])}, '
        f'account ID {c.esc(s["account_id"])}</div>'
        f'<div style="margin-top:10px"><b>Suggested line:</b> {c.esc(titles.get(suggested, "none"))} '
        f'<span class="muted">({c.esc(sections.get(suggested, ""))}, {conf}, from {c.esc(s["provider"])})</span></div>'
        f'<div style="margin-top:4px">{c.esc(s["reason"])}</div></div>')
    keys = [line["key"] for line in lines]
    a, b = st.columns([2, 1])
    choice = a.selectbox("Line", keys, index=keys.index(suggested) if suggested in keys else 0,
                         format_func=lambda k: f"{titles[k]} ({sections[k]})", key=f"line-{s['id']}")
    b.write("")
    if b.button("Approve and re-run", key=f"approve-{s['id']}", type="primary"):
        result = c.post(f"/suggestions/{s['id']}/approve", {"actor": c.actor(), "line_key": choice})
        run = result.get("run")
        msg = f'Mapped {s["account_name"]} to {titles[choice]}.'
        if run:
            msg += f' Run #{run["id"]} finished: {run["status"]}.'
        st.session_state.flash = msg
        st.rerun()
    with st.expander("Reject instead"):
        note = st.text_input("Why", key=f"note-{s['id']}", placeholder="e.g. account opened in error")
        if st.button("Reject", key=f"reject-{s['id']}", disabled=not note):
            c.post(f"/suggestions/{s['id']}/reject", {"actor": c.actor(), "note": note})
            st.session_state.flash = f'Rejected the suggestion for {s["account_name"]}.'
            st.rerun()

decided = c.get("/suggestions", status="decided")
if decided:
    st.subheader("Recent decisions")
    rows = []
    for s in decided:
        changed = s["decided_line"] and s["decided_line"] != s["suggested_line"]
        rows.append([c.esc(c.when(s["decided_at"])), c.esc(s["client_name"]), c.esc(s["account_name"]),
                     c.esc(titles.get(s["suggested_line"], "none")),
                     c.esc(titles.get(s["decided_line"], "rejected")) + (" (changed)" if changed else ""),
                     c.esc(c.who(s["decided_by"])), c.esc(s["provider"])])
    c.html_block(c.table([("Decided", ""), ("Client", ""), ("Account", ""), ("Suggested", ""), ("Decision", ""),
                          ("By", ""), ("Suggested by", "")], rows))
