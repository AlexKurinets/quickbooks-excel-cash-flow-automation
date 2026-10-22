"""The HTTP API over the seeded state. The approval test at the bottom changes state."""

from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from cashflow.api import app
from conftest import latest


@pytest.fixture(scope="module")
def api(seeded):
    return TestClient(app)


def test_health_and_close_status(api):
    assert api.get("/health").json() == {"ok": True, "source": "synthetic"}
    close = api.get("/close", params={"period": "2026-09"}).json()
    assert close["counts"] == {"ready": 5, "held": 3, "failed": 0, "sent": 0, "not_run": 0}
    assert close["pending_mappings"] == 1
    north = next(c for c in close["clients"] if c["client_id"] == "northvane-mechanical")
    assert north["warnings"] == 1 and north["warning"].startswith("August changed")
    assert api.get("/periods").json() == ["2026-09", "2026-08"]
    assert api.get("/close", params={"period": "Sept"}).status_code == 422


def test_run_detail_workbook_download_and_preview(api, seeded):
    run = latest(seeded["conn"], "quillmark-studio")
    detail = api.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "ready" and len(detail["checks"]) == 9 and detail["formula_count"] == 172
    r = api.get(f"/runs/{run['id']}/workbook")
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml")
    assert 'filename="Quillmark Studio LLC - Cash Flow - 2026-09.xlsx"' in r.headers["content-disposition"]
    assert openpyxl.load_workbook(io.BytesIO(r.content))["Cash Flow"]["B2"].value == "Quillmark Studio LLC"
    same = api.get("/clients/quillmark-studio/workbook", params={"period": "2026-09"})
    assert same.content == r.content
    preview = api.get(f"/runs/{run['id']}/preview").json()
    rows = {row["label"]: row["values"] for row in preview["rows"]}
    assert preview["columns"][:2] == ["Jan", "Feb"] and preview["columns"][12] == "Year to date"
    assert rows["Cash at end of period"][8] == detail["figures"]["qbo"]["bs_cash"][8]
    assert rows["Increase (decrease) in deferred revenue"][0] != 0   # this client bills retainers in advance


def test_held_run_has_no_workbook_and_cannot_be_sent(api, seeded):
    run = latest(seeded["conn"], "rye-and-ember-bakery")
    assert api.get(f"/runs/{run['id']}/workbook").status_code == 409
    assert api.post(f"/runs/{run['id']}/deliver", json={"actor": "mara.quist@example.com"}).status_code == 409
    assert api.get("/runs/999999").status_code == 404


def test_template_endpoint_lists_what_the_writer_may_touch(api):
    t = api.get("/template").json()
    assert t["formula_count"] == 172 and t["formulas_by_sheet"] == {"Cash Flow": 106, "Summary": 66, "Lists": 0}
    writers = {p["part"]: p["writer"] for p in t["parts"]}
    assert writers["xl/charts/chart1.xml"] == "never touched"
    assert writers["xl/workbook.xml"] == "never touched"
    assert writers["xl/worksheets/sheet1.xml"] == "input cells and formula results only"
    assert sum(1 for w in writers.values() if w == "never touched") == 14
    assert t["problem"] is None


def test_approve_endpoint_maps_and_reruns(api):
    pending = api.get("/suggestions").json()
    assert len(pending) == 1
    result = api.post(f"/suggestions/{pending[0]['id']}/approve", json={"actor": "mara.quist@example.com"}).json()
    assert result["suggestion"]["decided_line"] == "loans"
    assert result["run"]["status"] == "ready"
    assert api.get("/suggestions").json() == []
    decided = api.get("/suggestions", params={"status": "decided"}).json()
    assert decided[0]["decided_by"] == "mara.quist@example.com"
    again = api.post(f"/suggestions/{pending[0]['id']}/approve", json={"actor": "mara.quist@example.com"})
    assert again.status_code == 409
    sent = api.post(f"/runs/{result['run']['id']}/deliver", json={"actor": "mara.quist@example.com"}).json()
    assert sent["delivered_by"] == "mara.quist@example.com"
