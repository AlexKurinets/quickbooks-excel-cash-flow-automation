"""Streamlit dashboard: the month-end close as a reviewer at the firm sees it."""

from __future__ import annotations

import streamlit as st

import common

st.set_page_config(page_title="Cash flow close", page_icon="📒", layout="wide")
common.setup()

with st.sidebar:
    st.markdown("### Cash flow close")
    periods = common.get("/periods")
    if periods:
        st.selectbox("Month", periods, key="period", format_func=common.period_label)
    people = common.staff()
    st.selectbox("Signed in as", list(people), key="actor", format_func=lambda e: people[e]["name"])
    st.caption("Synthetic data. Every client, person and figure is made up.")

pages = [
    st.Page("views/close.py", title="Close status", default=True),  # the default page is served at /
    st.Page("views/run.py", title="Run detail", url_path="run"),
    st.Page("views/mapping.py", title="Mapping review", url_path="mapping"),
    st.Page("views/template.py", title="Template", url_path="template"),
]
st.navigation(pages).run()
