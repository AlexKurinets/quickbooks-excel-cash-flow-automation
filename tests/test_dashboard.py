"""Every dashboard page renders against a live API over the seeded state.

The API runs as a separate uvicorn process, the way the Compose stack runs it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest
import requests

from conftest import DSN, HOME, latest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

VIEWS = HOME / "dashboard" / "views"


@pytest.fixture(scope="module")
def api_url(seeded):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "CASHFLOW_DSN": DSN, "CASHFLOW_SOURCE": "synthetic"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "cashflow.api:app", "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(f"{url}/health", timeout=1).ok:
                break
        except requests.ConnectionError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("API did not start")
    os.environ["CASHFLOW_API_URL"] = url
    sys.path.insert(0, str(HOME / "dashboard"))
    sys.modules.pop("common", None)
    yield url
    proc.terminate()
    proc.wait(10)


def render(page: str, **query) -> AppTest:
    at = AppTest.from_file(str(VIEWS / page), default_timeout=60)
    for k, v in query.items():
        at.query_params[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown) + "\n".join(t.value for t in at.title)


def test_close_status_page(api_url):
    at = render("close.py")
    body = text(at)
    assert "September 2026 close" in body
    assert "Equipment Loan - Mower Fleet" in body and "expired on 2 Oct 2026" in body
    assert "&#36;2,350.00" in body  # dollar signs are entities, so markdown never turns them into LaTeX


@pytest.mark.parametrize("client_id", ["wrenfield-dental", "birchline-grounds", "rye-and-ember-bakery"])
def test_run_detail_page(api_url, seeded, client_id):
    run = latest(seeded["conn"], client_id)
    at = render("run.py", run=str(run["id"]))
    body, headings = text(at), [s.value for s in at.subheader]
    assert "Checks" in headings and "Run history" in headings
    if run["status"] == "ready":
        assert "Tie-out by month" in headings
        assert "Cash at end of period" in body
    else:
        assert "Held." in body


def test_mapping_and_template_pages(api_url):
    mapping = render("mapping.py")
    assert "Equipment Loan - Mower Fleet" in text(mapping)
    assert any(b.label == "Approve and re-run" for b in mapping.button)
    template = render("template.py")
    assert "172" in text(template) and "never touched" in text(template)
