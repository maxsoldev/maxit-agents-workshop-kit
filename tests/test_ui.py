"""UI tests: each page renders, cases round-trip to cases.jsonl, validation errors show. Uses a fake runner."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kit import config
from tests import ui_fake_runner

EMAIL = """From: Michael Weber <m.weber@nodehost.example>
To: Info <info@example.org>
Subject: Managed validator nodes
Date: Wed, 04 Nov 2026 10:05:00 +0100
Content-Type: text/plain; charset="utf-8"

Guten Tag, wir bieten Nodes an.
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    uc = tmp_path / "usecases" / "demo"
    (uc / "data" / "emails").mkdir(parents=True)
    (uc / "data" / "emails" / "06.eml").write_text(EMAIL, encoding="utf-8")
    case = {"id": "m06", "title": "Service offer", "today": "2026-11-04", "input": {"email_file": "data/emails/06.eml"},
            "expected": {"category": "service_offer"},
            "checks": [{"type": "equals", "field": "category"},
                       {"type": "judge", "field": "reply", "rubric": "Polite no."}]}
    (uc / "cases.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "USECASES_DIR", tmp_path / "usecases")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("LANGFUSE", "off")
    ui_fake_runner.install()
    from kit.ui import app as ui
    ui.JOBS.clear()
    return TestClient(ui.app)


def cases_file() -> list[dict]:
    lines = (config.USECASES_DIR / "demo" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def form(**over) -> dict:
    data = {"original_id": "", "id": "m07", "title": "New one", "today": "",
            "input": '{"email_file": "data/emails/06.eml"}', "expected": '{"category": "spam"}',
            "check_type": ["equals", "in"], "check_field": ["category", "category"],
            "check_arg": ["", "spam, phishing"], "check_min": ["", ""]}
    data.update(over)
    return data


def test_root_redirects(client):
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/uc/demo/cases"


def test_cases_page_shows_email(client):
    r = client.get("/uc/demo/cases")
    assert r.status_code == 200
    assert "Managed validator nodes" in r.text and "Guten Tag" in r.text
    assert "Langfuse" not in r.text  # hidden when off


def test_forms_render(client):
    assert "Guten Tag" in client.get("/uc/demo/cases/m06/edit").text
    assert 'value="m06-copy"' in client.get("/uc/demo/cases/new?copy=m06").text
    assert client.get("/uc/demo/file?path=data/emails/06.eml").text.count("Managed validator") == 1
    assert "File not found" in client.get("/uc/demo/file?path=../../etc/passwd").text


def test_add_edit_delete_round_trip(client):
    r = client.post("/uc/demo/cases/save", data=form(), follow_redirects=False)
    assert r.status_code == 303
    new = cases_file()[1]
    assert new["id"] == "m07"
    assert new["checks"][1] == {"type": "in", "field": "category", "values": ["spam", "phishing"]}

    r = client.post("/uc/demo/cases/save", data=form(original_id="m07", title="Renamed", today="2026-11-05"),
                    follow_redirects=False)
    assert r.status_code == 303
    assert [c["id"] for c in cases_file()] == ["m06", "m07"]
    assert cases_file()[1]["title"] == "Renamed" and cases_file()[1]["today"] == "2026-11-05"

    client.post("/uc/demo/cases/m07/delete")
    assert [c["id"] for c in cases_file()] == ["m06"]


def test_validation_errors_show(client):
    r = client.post("/uc/demo/cases/save", data=form(title="", input="{bad json"))
    assert r.status_code == 422
    assert "Not saved" in r.text and "input: not valid JSON" in r.text
    r = client.post("/uc/demo/cases/save", data=form(title=""))
    assert "title: required" in r.text
    r = client.post("/uc/demo/cases/save", data=form(id="m06"))
    assert "is used by another case" in r.text
    assert len(cases_file()) == 1


def test_run_results_runs(client):
    r = client.post("/uc/demo/run", data={"engine": "python", "name": "first"}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(50):
        if "Open results" in client.get("/uc/demo/run/progress").text:
            break
        time.sleep(0.05)
    assert "Open results" in client.get("/uc/demo/run").text
    runs = client.get("/uc/demo/runs")
    assert runs.status_code == 200 and "first" in runs.text

    run_id = ui_fake_runner.list_runs("demo")[0]["run_id"]
    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200 and "Service offer" in page.text
    assert client.get("/runs/nope").status_code == 404


def test_compare_marks_changes(client):
    from kit.ui import app as ui
    a = {"cases": [{"id": "x", "passed": True}, {"id": "y", "passed": False}, {"id": "z", "passed": True}]}
    b = {"cases": [{"id": "x", "passed": False}, {"id": "y", "passed": False}]}
    assert ui.compare_cases(a, b) == {"x": "better", "y": "same", "z": "new"}


def test_docs_page(client):
    assert client.get("/docs").status_code == 200
