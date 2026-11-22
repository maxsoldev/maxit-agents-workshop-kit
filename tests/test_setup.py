"""Unit tests for the pure parts of kit/setup.py (no Docker, no network)."""
import json
import sys
import types

from kit import setup
from kit.setup import compose_profiles, fill_placeholders, read_env, same_workflow, set_env_key, workflow_body


def test_check_with_a_fake_runner(tmp_path, monkeypatch, capsys):
    uc = tmp_path / "demo"
    for engine in ("n8n", "python"):
        (uc / engine).mkdir(parents=True)
    (uc / "cases.jsonl").write_text('{"id": "d1"}\n{"id": "d2"}\n')
    calls = []

    def run_eval(usecase, engine, case_ids=None, name=None, on_progress=None):
        calls.append((usecase, engine, case_ids))
        case = {"id": "d1", "passed": engine == "python", "error": "HTTP 404" if engine == "n8n" else None,
                "checks": []}
        return {"cases": [case]}

    monkeypatch.setitem(sys.modules, "kit.runner", types.SimpleNamespace(run_eval=run_eval))
    monkeypatch.setattr(setup, "USECASES_DIR", tmp_path)
    monkeypatch.setattr(setup, "usecases", lambda: ["demo"])
    monkeypatch.setattr(setup, "_port_open", lambda port: True)
    monkeypatch.setenv("N8N_API_KEY", "x")
    assert setup.check() == 1
    out = capsys.readouterr().out
    assert calls == [("demo", "n8n", ["d1"]), ("demo", "python", ["d1"])]
    assert "FAIL  demo/n8n (d1): HTTP 404" in out
    assert "OK    demo/python (d1)" in out
    assert "SKIP  demo/claude (d1)" in out


def test_read_env_skips_comments_and_strips_quotes(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\n\nA=1\nB = 'two'\nexport C=\"x=y\"\nnot a line\n")
    assert read_env(f) == {"A": "1", "B": "two", "C": "x=y"}


def test_read_env_missing_file(tmp_path):
    assert read_env(tmp_path / "nope") == {}


def test_set_env_key_replaces_and_appends(tmp_path, monkeypatch):
    monkeypatch.delenv("N8N_API_KEY", raising=False)
    monkeypatch.delenv("NEW_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("# keep me\nN8N_API_KEY=\nOTHER=1\nN8N_API_KEY=stale\n")
    set_env_key(f, "N8N_API_KEY", "abc")
    set_env_key(f, "NEW_KEY", "z")
    assert f.read_text() == "# keep me\nN8N_API_KEY=abc\nOTHER=1\nNEW_KEY=z\n"


def test_compose_profiles():
    assert compose_profiles({}) == ["n8n", "langfuse"]
    assert compose_profiles({"N8N": "local", "LANGFUSE": "off"}) == ["n8n"]
    assert compose_profiles({"N8N": "hosted", "LANGFUSE": " Local "}) == ["langfuse"]
    assert compose_profiles({"N8N": "hosted", "LANGFUSE": "hosted"}) == []


def test_fill_placeholders_keeps_n8n_expressions():
    text = json.dumps({"cred": "{{OPENAI_CREDENTIAL_ID}}", "url": "{{KIT_TOOLS_URL}}", "expr": "={{ $json.body }}"})
    out = json.loads(fill_placeholders(text, "c42", "http://host.docker.internal:5701/mcp"))
    assert out == {"cred": "c42", "url": "http://host.docker.internal:5701/mcp", "expr": "={{ $json.body }}"}


def test_workflow_body_and_same_workflow():
    wf = {"name": "Kit: marketing agent", "id": "x", "active": True, "pinData": {},
          "nodes": [{"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                     "parameters": {"path": "marketing"}}],
          "connections": {}}
    body = workflow_body(wf)
    assert set(body) == {"name", "nodes", "connections", "settings"}
    # n8n adds ids and positions: still the same workflow.
    stored = {**body, "nodes": [{**body["nodes"][0], "id": "n1", "position": [0, 0], "webhookId": "w"}]}
    assert same_workflow(stored, body)
    changed = {**body, "nodes": [{**body["nodes"][0], "parameters": {"path": "other"}}]}
    assert not same_workflow(changed, body)
