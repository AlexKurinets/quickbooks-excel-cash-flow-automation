"""Template: what the writer may touch in the house workbook, and what it never does."""

import streamlit as st

import common as c

t = c.get("/template")
st.title("Template")
st.caption("The house workbook every run fills. The writer changes only the input cells listed here and the "
           "cached results of formula cells. Every other part of the file is copied byte for byte, and each run "
           "checks that it was.")
if t["problem"]:
    c.html_block(f'<div class="banner held"><b>The template no longer matches its map.</b> {c.esc(t["problem"])}</div>')

untouched = sum(1 for p in t["parts"] if p["writer"] == "never touched")
c.html_block(c.tiles([
    (str(t["formula_count"]), "formulas, never written over", "#1f5f8b"),
    (str(len(t["inputs"])), "input rows, found by label", "#1f7a4d"),
    (f"{untouched} of {len(t['parts'])}", "package parts never touched", "#555555"),
    (t["sha256"][:10], f"fingerprint of {t['name']}", "#555555"),
]))
st.markdown(" · ".join(f"{name}: {n} formulas" for name, n in t["formulas_by_sheet"].items()))

left, right = st.columns(2)
with left:
    st.subheader("Inputs the automation fills")
    rows = [[c.esc(i["label"]), str(i["row"]), c.esc(i["months"])] for i in t["inputs"]]
    rows += [[c.esc(f["label"]), c.esc(f["cell"]), "single cell"] for f in t["fields"]]
    c.html_block(c.table([("Row label", ""), ("Row", "num"), ("Months", "")], rows))
with right:
    st.subheader("Formula rows the checks read back")
    rows = [[c.esc(o["label"]), str(o["row"])] for o in t["outputs"]]
    c.html_block(c.table([("Row label", ""), ("Row", "num")], rows))
    st.subheader("Package parts")
    rows = [[f'<code>{c.esc(p["part"])}</code>', f'{p["bytes"]:,}', c.esc(p["writer"])] for p in t["parts"]]
    c.html_block(c.table([("Part", ""), ("Bytes", "num"), ("What the writer may change", "")], rows))
