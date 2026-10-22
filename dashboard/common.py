"""Shared helpers for the dashboard pages: API calls, formatting, and the small HTML pieces."""

from __future__ import annotations

import html
import os
from datetime import datetime

import requests
import streamlit as st

API = os.environ.get("CASHFLOW_API_URL", "http://localhost:8000").rstrip("/")
DOLLAR = "&#36;"  # a literal $ in HTML; two of them in markdown would turn into a LaTeX formula

STATUS = {
    "ready": ("Ready", "#1f7a4d", "#e3f4ea"),
    "held": ("Held", "#a3361c", "#fbe6df"),
    "failed": ("Failed", "#7a1f1f", "#f6dede"),
    "running": ("Running", "#555555", "#eeeeee"),
    None: ("Not run", "#555555", "#eeeeee"),
}
CHECK_ICON = {"pass": ("✓", "#1f7a4d"), "fail": ("✕", "#a3361c"), "warn": ("!", "#9a6a00"), "skipped": ("–", "#888888")}

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1180px;}
.cf-tiles {display: flex; gap: 14px; margin: 6px 0 18px 0; flex-wrap: wrap;}
.cf-tile {flex: 1 1 150px; border: 1px solid #e2e2dc; border-radius: 10px; padding: 12px 16px; background: #fff;}
.cf-tile .n {font-size: 1.9rem; font-weight: 650; line-height: 1.1;}
.cf-tile .l {color: #555; font-size: 0.86rem;}
table.cf {border-collapse: collapse; width: 100%; font-size: 0.9rem; background: #fff;}
table.cf th {text-align: left; font-weight: 600; color: #333; border-bottom: 2px solid #d9d9d2; padding: 7px 9px;}
table.cf td {border-bottom: 1px solid #ecece6; padding: 7px 9px; vertical-align: top;}
table.cf td.num, table.cf th.num {text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap;}
table.cf tr.bold td {font-weight: 650;}
.cf-scroll {overflow-x: auto; margin-bottom: 8px;}
table.cf.wide {font-size: 0.74rem;}
table.cf.wide td:first-child {min-width: 205px;}
table.cf.wide td, table.cf.wide th {padding: 4px 5px;}
table.cf tr.section td {background: #f1f4f7; font-weight: 600;}
table.cf td.off {color: #a3361c; font-weight: 650;}
.chip {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600;}
.check {display: flex; gap: 12px; padding: 9px 0; border-bottom: 1px solid #ecece6;}
.check .icon {width: 24px; height: 24px; border-radius: 50%; color: #fff; text-align: center; font-weight: 700;
              line-height: 24px; flex: 0 0 24px; font-size: 0.85rem;}
.check .t {font-weight: 600;}
.check .s {color: #333; font-size: 0.92rem;}
.muted {color: #666; font-size: 0.86rem;}
.banner {border-radius: 8px; padding: 10px 14px; margin: 4px 0 14px 0; font-size: 0.95rem;}
.banner.held {background: #fbe6df; border-left: 4px solid #a3361c;}
.banner.warn {background: #fdf3d8; border-left: 4px solid #c99400;}
.banner.ready {background: #e3f4ea; border-left: 4px solid #1f7a4d;}
.card {border: 1px solid #e2e2dc; border-radius: 10px; padding: 14px 16px; background: #fff; margin-bottom: 10px;}
</style>
"""


def setup() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def get(path: str, **params):
    r = requests.get(f"{API}{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def get_bytes(path: str) -> bytes:
    r = requests.get(f"{API}{path}", timeout=60)
    r.raise_for_status()
    return r.content


def post(path: str, body: dict):
    r = requests.post(f"{API}{path}", json=body, timeout=120)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(detail)
    return r.json()


def esc(text) -> str:
    return html.escape("" if text is None else str(text)).replace("$", DOLLAR)


def money(value, blank: str = "") -> str:
    if value in (None, ""):
        return blank
    v = float(value)
    if abs(v) < 0.005:
        return "–"
    text = f"{DOLLAR}{abs(v):,.2f}"
    return f"({text})" if v < 0 else text


def when(value) -> str:
    if not value:
        return ""
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return f"{d.day} {d:%b %Y, %H:%M} UTC"


def chip(status) -> str:
    label, fg, bg = STATUS.get(status, STATUS[None])
    return f'<span class="chip" style="color:{fg};background:{bg}">{label}</span>'


def table(headers: list[tuple[str, str]], rows: list[list[str]], row_classes: list[str] | None = None,
          wide: bool = False) -> str:
    head = "".join(f'<th class="{cls}">{esc(h)}</th>' for h, cls in headers)
    body = []
    for i, row in enumerate(rows):
        cls = row_classes[i] if row_classes else ""
        cells = "".join(f'<td class="{headers[j][1]}">{cell}</td>' for j, cell in enumerate(row))
        body.append(f'<tr class="{cls}">{cells}</tr>')
    markup = f'<table class="cf{" wide" if wide else ""}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
    return f'<div class="cf-scroll">{markup}</div>'


def tiles(items: list[tuple[str, str, str]]) -> str:
    return '<div class="cf-tiles">' + "".join(
        f'<div class="cf-tile"><div class="n" style="color:{color}">{esc(n)}</div><div class="l">{esc(label)}</div></div>'
        for n, label, color in items) + "</div>"


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def staff() -> dict:
    if "staff" not in st.session_state:
        people = get("/staff")
        st.session_state.staff = people
    return {p["email"]: p for p in st.session_state.staff}


def who(email: str | None) -> str:
    if not email:
        return ""
    person = staff().get(email)
    return person["name"] if person else email


def actor() -> str:
    return st.session_state.get("actor") or "mara.quist@example.com"


def period() -> str | None:
    if st.session_state.get("period"):
        return st.session_state.period
    periods = get("/periods")
    return periods[0] if periods else None


def period_label(p: str) -> str:
    return datetime.strptime(p, "%Y-%m").strftime("%B %Y")
