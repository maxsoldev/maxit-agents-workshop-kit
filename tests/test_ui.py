"""UI tests: each page renders, cases round-trip to cases.jsonl, validation errors show. Uses a fake runner."""

from __future__ import annotations

import json
import time

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
    case = {
        "id": "m06",
        "title": "Service offer",
        "today": "2026-11-04",
        "input": {"email_file": "data/emails/06.eml"},
        "expected": {"category": "service_offer"},
        "checks": [
            {"type": "equals", "field": "category"},
            {"type": "judge", "field": "reply", "rubric": "Polite no."},
        ],
    }
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
    data = {
        "original_id": "",
        "id": "m07",
        "title": "New one",
        "today": "",
        "input": '{"email_file": "data/emails/06.eml"}',
        "expected": '{"category": "spam"}',
        "check_type": ["equals", "in"],
        "check_field": ["category", "category"],
        "check_arg": ["", "spam, phishing"],
        "check_min": ["", ""],
    }
    data.update(over)
    return data


def test_root_redirects(client):
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/uc/demo/cases"


def test_no_key_banner(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert "judge checks are skipped" in client.get("/uc/demo/cases").text
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert "judge checks are skipped" not in client.get("/uc/demo/cases").text


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


def test_previews_show_every_file(client):
    inp = json.dumps({"email_file": "data/emails/06.eml", "email_2_file": "data/emails/06.eml"})
    text = client.post("/uc/demo/previews", data={"input": inp}).text
    assert text.count("Managed validator") == 2 and "email_2_file" in text
    assert "email_2_file" in client.get("/uc/demo/cases/new").text  # the hint about numbered keys


def test_count_and_any_from_the_form(client):
    data = form(
        check_type=["count", "count", "any", "any"],
        check_field=["tasks"] * 4,
        check_arg=[
            "2",
            '{"min": 1, "max": 3}',
            '{"kind": "trip"}',
            '{"where": {"kind": "trip"}, "contains": {"event": "Riga"}}',
        ],
        check_min=[""] * 4,
    )
    assert client.post("/uc/demo/cases/save", data=data, follow_redirects=False).status_code == 303
    assert cases_file()[1]["checks"] == [
        {"type": "count", "field": "tasks", "equals": 2},
        {"type": "count", "field": "tasks", "min": 1, "max": 3},
        {"type": "any", "field": "tasks", "where": {"kind": "trip"}},
        {"type": "any", "field": "tasks", "where": {"kind": "trip"}, "contains": {"event": "Riga"}},
    ]
    page = client.get("/uc/demo/cases/m07/edit").text
    assert 'value="2"' in page and "Riga" in page


def test_add_edit_delete_round_trip(client):
    r = client.post("/uc/demo/cases/save", data=form(), follow_redirects=False)
    assert r.status_code == 303
    new = cases_file()[1]
    assert new["id"] == "m07"
    assert new["checks"][1] == {"type": "in", "field": "category", "values": ["spam", "phishing"]}

    r = client.post(
        "/uc/demo/cases/save", data=form(original_id="m07", title="Renamed", today="2026-11-05"), follow_redirects=False
    )
    assert r.status_code == 303
    assert [c["id"] for c in cases_file()] == ["m06", "m07"]
    assert cases_file()[1]["title"] == "Renamed" and cases_file()[1]["today"] == "2026-11-05"

    client.post("/uc/demo/cases/m07/delete")
    assert [c["id"] for c in cases_file()] == ["m06"]


def test_validation_errors_show(client):
    r = client.post("/uc/demo/cases/save", data=form(title="", input="{bad json"))
    assert r.status_code == 422
    assert "Not saved" in r.text and "Input is not valid JSON" in r.text and "double" in r.text
    r = client.post("/uc/demo/cases/save", data=form(title="", today="2026-11-17"))
    assert "Title is empty" in r.text and 'value="2026-11-17"' in r.text  # the form keeps what you typed
    r = client.post("/uc/demo/cases/save", data=form(input="{}"))
    assert "Input is empty" in r.text
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


def test_check_runs_hidden_and_compare_default(client):
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)

    def save(run_id, engine, name, ids):
        run = {
            "run_id": run_id,
            "name": name,
            "usecase": "demo",
            "engine": engine,
            "started_at": run_id,
            "summary": {"cases": len(ids), "passed": 0, "by_check": {}},
            "cases": [
                {"id": i, "title": i, "passed": False, "checks": [], "input": {}, "expected": {}, "output": {}}
                for i in ids
            ],
        }
        (config.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(run))

    save("20261124-100000-demo-n8n", "n8n", "base", ["m06", "m07"])
    save("20261124-100500-demo-python", "python", "py", ["m06", "m07"])
    save("20261124-101000-demo-n8n", "n8n", "check", ["m06"])
    save("20261124-101500-demo-n8n", "n8n", "small", ["m06"])
    save("20261124-102000-demo-n8n", "n8n", "now", ["m06", "m07"])
    runs = client.get("/uc/demo/runs").text
    assert ">check<" not in runs and "1 <code>make check</code> run(s) hidden" in runs
    assert ">check<" in client.get("/uc/demo/runs?checks=1").text
    page = client.get("/runs/20261124-102000-demo-n8n").text
    # default: the last n8n run with the same cases (not "small", which has other cases; not the check run)
    assert 'value="20261124-100000-demo-n8n" selected' in page
    assert "small · " in page and "other cases" in page and "20261124-101000-demo-n8n" not in page
    assert page.index("small") < page.index("py ·")  # the same engine first
    save("20261124-103000-demo-n8n", "n8n", "one", ["m07"])  # no run with the same cases: the last full run
    assert 'value="20261124-102000-demo-n8n" selected' in client.get("/runs/20261124-103000-demo-n8n").text


def test_compare_marks_changes(client):
    from kit.ui import app as ui

    a = {"cases": [{"id": "x", "passed": True}, {"id": "y", "passed": False}, {"id": "z", "passed": True}]}
    b = {"cases": [{"id": "x", "passed": False}, {"id": "y", "passed": False}]}
    assert ui.compare_cases(a, b) == {"x": "better", "y": "same", "z": "new"}
    a["cases"][0]["case_hash"], b["cases"][0]["case_hash"] = "h2", "h1"
    assert ui.changed_cases(a, b) == {"x"}


def test_runs_list_says_new_and_changed_cases(client):
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for run_id, hashes in (
        ("20261124-100000-demo-n8n", {"m06": "a"}),
        ("20261124-101000-demo-n8n", {"m06": "b", "m07": "c", "m08": "d"}),
    ):
        run = {
            "run_id": run_id,
            "name": run_id[-8:],
            "usecase": "demo",
            "engine": "n8n",
            "started_at": run_id,
            "summary": {"cases": len(hashes), "passed": 0, "by_check": {}},
            "cases": [
                {"id": i, "case_hash": h, "passed": False, "checks": [], "input": {}, "expected": {}, "output": {}}
                for i, h in hashes.items()
            ],
        }
        (config.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(run))
    page = client.get("/uc/demo/runs").text
    assert "3 cases (2 new, 1 changed)" in page
    assert "case changed" in client.get("/runs/20261124-101000-demo-n8n").text


def test_picker_lists_single_messages_and_hides_rules(client):
    from kit.ui import app as ui

    data = config.USECASES_DIR / "demo" / "data"
    (data / "rules.md").write_text("# Rules\n")
    (data / "mattermost_messages.txt").write_text(
        "Header line\n\n--- Direktnachricht mit Priya Nair ---\nPriya Nair  10.11.2026, 13:22\nHi Martina,\nRiga?\n\n"
        "Priya Nair  18.11.2026, 10:30\nUpdate: Riga fällt aus.\n",
        encoding="utf-8",
    )
    picks = ui.input_picks("demo")
    assert [p["kind"] for p in picks] == ["file", "message", "message"]
    assert picks[0]["date"] == "2026-11-04"  # from the Date header of the email
    assert picks[2] == {
        "label": "mattermost: Priya Nair, 2026-11-18: Update: Riga fällt aus.",
        "kind": "message",
        "channel": "mattermost",
        "text": "Priya Nair  18.11.2026, 10:30\nUpdate: Riga fällt aus.",
        "date": "2026-11-18",
    }
    page = client.get("/uc/demo/cases/new").text
    assert "rules.md" not in page and "Or paste a message" in page and 'placeholder="m07"' in page


def test_checks_in_words():
    from kit.ui.app import check_words, field_words

    assert field_words("tasks.0.kind") == "tasks #1 → kind"
    exp = {"tasks": [{"kind": "leave"}], "reply": ""}
    assert check_words({"type": "equals", "field": "tasks.0.kind"}, exp) == 'is "leave" (from expected)'
    assert check_words({"type": "equals", "field": "reply"}, exp) == 'is "" (from expected)'
    assert check_words({"type": "count", "field": "tasks", "min": 1, "max": 2}, {}) == "has 1 to 2 items"
    assert (
        check_words({"type": "any", "field": "tasks", "where": {"kind": "trip"}, "contains": {"event": "Riga"}}, {})
        == 'has an item where kind is "trip" and event contains "Riga"'
    )


def test_docs_page(client):
    assert client.get("/docs").status_code == 200


def test_start_page_opens_the_last_used_use_case(client):
    (config.USECASES_DIR / "zeta").mkdir()
    (config.USECASES_DIR / "zeta" / "cases.jsonl").write_text("", encoding="utf-8")
    assert client.get("/", follow_redirects=False).headers["location"] == "/uc/demo/cases"  # the first one
    client.get("/uc/zeta/cases")
    assert client.get("/", follow_redirects=False).headers["location"] == "/uc/zeta/cases"
    client.cookies.clear()
    client.cookies.set("kit_last_uc", "gone")  # a use case that is not there any more
    assert client.get("/", follow_redirects=False).headers["location"] == "/uc/demo/cases"
