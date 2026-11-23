"""Unit tests for the pure parts of kit/setup.py (no Docker, no network)."""

import json
import sys
import threading
import time
import types

import httpx

from kit import setup
from kit.setup import compose_profiles, fill_placeholders, read_env, same_workflow, set_env_key, workflow_body


def test_check_with_a_fake_runner(tmp_path, monkeypatch, capsys):
    uc = tmp_path / "demo"
    for engine in ("n8n", "python"):
        (uc / engine).mkdir(parents=True)
    (uc / "cases.jsonl").write_text('{"id": "d1"}\n{"id": "d2"}\n')
    calls = []
    both_running = threading.Barrier(2, timeout=5)  # raises BrokenBarrierError if the runs are not parallel

    def run_eval(usecase, engine, case_ids=None, name=None, on_progress=None):
        calls.append((usecase, engine, case_ids))
        both_running.wait()
        if engine == "n8n":
            time.sleep(0.2)  # n8n ends last, but its line must still come first
        checks = [
            {"type": "equals", "field": "kind", "passed": True, "reason": "got leave"},
            {"type": "equals", "field": "who", "passed": False, "reason": "got Ann, expected Bob"},
            {"type": "judge", "field": "draft", "passed": None, "reason": "judge skipped", "skipped": True},
        ]
        case = {
            "id": "d1",
            "passed": False,
            "error": "HTTP 404" if engine == "n8n" else None,
            "checks": [] if engine == "n8n" else checks,
        }
        return {"cases": [case]}

    monkeypatch.setitem(sys.modules, "kit.runner", types.SimpleNamespace(run_eval=run_eval))
    monkeypatch.setattr(setup, "USECASES_DIR", tmp_path)
    monkeypatch.setattr(setup, "usecases", lambda: ["demo"])
    monkeypatch.setattr(setup, "_port_open", lambda port: True)
    monkeypatch.setenv("N8N_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("KIT_ENGINES", "")  # all engines, whatever .env says
    assert setup.check() == 1
    out = capsys.readouterr().out
    assert sorted(calls) == [("demo", "n8n", ["d1"]), ("demo", "python", ["d1"])]
    lines = out.splitlines()
    assert lines[1] == "Running 2 checks in parallel (one case each; this can take a few minutes) ..."
    assert lines[2].startswith("ERROR    demo/n8n (d1): HTTP 404. Next: ")  # fixed order: n8n, claude, python
    assert lines[3].startswith("SKIP     demo/claude (d1)")
    # a wrong answer is not an error; a skipped judge does not count
    assert lines[4] == "ANSWERS  demo/python (d1): 1/2 checks pass. equals who: got Ann, expected Bob"
    assert lines[-1] == "1 engine(s) do not answer (ERROR lines). Fix them, then run make check again."


def test_check_says_wrong_answers_are_fine(tmp_path, monkeypatch, capsys):
    (tmp_path / "demo" / "python").mkdir(parents=True)
    (tmp_path / "demo" / "cases.jsonl").write_text('{"id": "d1"}\n')
    case = {"id": "d1", "passed": True, "error": None, "checks": [{"type": "equals", "passed": True}]}
    run_eval = lambda *a, **k: {"cases": [case]}  # noqa: E731
    monkeypatch.setitem(sys.modules, "kit.runner", types.SimpleNamespace(run_eval=run_eval))
    monkeypatch.setattr(setup, "USECASES_DIR", tmp_path)
    monkeypatch.setattr(setup, "usecases", lambda: ["demo"])
    monkeypatch.setattr(setup, "_port_open", lambda port: True)
    monkeypatch.setenv("KIT_ENGINES", "python")
    assert setup.check() == 0
    lines = capsys.readouterr().out.splitlines()
    assert "ANSWERS  demo/python (d1): all checks pass" in lines
    assert lines[-1].startswith("All 1 engines answer. Wrong answers are fine here")


def test_python_next_step_fits_the_error():
    def hint(text):
        return setup.next_step("python", f"agent.py exit 1: {text}", "marketing", "m22")

    assert "OPENAI_API_KEY" in hint("openai.AuthenticationError: Error code: 401 - invalid key")
    assert "OPENAI_BASE_URL" in hint("openai.APIConnectionError: Connection error.")
    assert "KIT_MODEL" in hint("openai.BadRequestError: Error code: 400 - Function tools ... not supported")
    assert "KIT_MODEL" in hint("openai.PermissionDeniedError: Error code: 403 - model not available")
    assert "credit" in hint("openai.RateLimitError: Error code: 429")
    assert hint("KeyError: 'email'") == "make run-case UC=marketing ENGINE=python CASE=m22 shows the agent log"
    assert "claude" in setup.next_step("claude", "Error code: 401")  # other engines keep their hint


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


def test_service_notes():
    from kit.setup import service_notes

    assert service_notes({}) == []
    assert service_notes({"KIT_ENGINES": "claude", "LANGFUSE": "hosted"}) == [
        "n8n: not used (KIT_ENGINES=claude), not started",
        "Langfuse: LANGFUSE=hosted in .env, not started here",
    ]
    assert service_notes({"N8N": "hosted", "LANGFUSE": "off"}) == [
        "n8n: N8N=hosted in .env, not started here",
        "Langfuse: LANGFUSE=off in .env, no traces",
    ]


def test_fill_placeholders_keeps_n8n_expressions():
    text = json.dumps({"cred": "{{OPENAI_CREDENTIAL_ID}}", "url": "{{KIT_TOOLS_URL}}", "expr": "={{ $json.body }}"})
    out = json.loads(fill_placeholders(text, "c42", "http://host.docker.internal:5701/mcp"))
    assert out == {"cred": "c42", "url": "http://host.docker.internal:5701/mcp", "expr": "={{ $json.body }}"}


def test_workflow_body_and_same_workflow():
    wf = {
        "name": "Kit: marketing agent",
        "id": "x",
        "active": True,
        "pinData": {},
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "parameters": {"path": "marketing"}}
        ],
        "connections": {},
    }
    body = workflow_body(wf)
    assert set(body) == {"name", "nodes", "connections", "settings"}
    # n8n adds ids and positions: still the same workflow.
    stored = {**body, "nodes": [{**body["nodes"][0], "id": "n1", "position": [0, 0], "webhookId": "w"}]}
    assert same_workflow(stored, body)
    changed = {**body, "nodes": [{**body["nodes"][0], "parameters": {"path": "other"}}]}
    assert not same_workflow(changed, body)


def test_model_key_check_names_the_error(monkeypatch):
    seen = []

    def get(url, headers, timeout):
        seen.append(url)
        if headers["Authorization"] == "Bearer good":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(401, json={"error": {"message": "Incorrect API key provided: sk-ba***ey."}})

    monkeypatch.setattr(setup.httpx, "get", get)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gw:4000/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "good")
    assert setup.model_key_works()[0] and seen == ["http://gw:4000/v1/models"]
    monkeypatch.setenv("OPENAI_API_KEY", "bad")
    works, text = setup.model_key_works()
    assert not works and "401" in text and "Incorrect API key" in text


def test_settings_read_env_file_each_time(tmp_path, monkeypatch):
    from kit import config

    monkeypatch.delenv("KIT_JUDGE_MODEL", raising=False)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("KIT_JUDGE_MODEL=first\n")
    assert config.settings().judge_model == "first"
    (tmp_path / ".env").write_text("KIT_JUDGE_MODEL=second\n")  # changed while the kit runs
    assert config.settings().judge_model == "second"
    monkeypatch.setenv("KIT_JUDGE_MODEL", "shell")  # the shell env wins
    assert config.settings().judge_model == "shell"


def test_export_puts_the_placeholders_back():
    from kit.config import ROOT, settings

    text = (ROOT / "usecases/travel/n8n/workflow.json").read_text()
    url = "http://host.docker.internal:5701/mcp"
    filled = json.loads(fill_placeholders(text, "cred123", url))
    assert "cred123" in json.dumps(filled) and "{{KIT_" not in json.dumps(filled)
    back = setup.placeholders_back(filled, "cred123", url, settings().model)
    assert back == json.loads(text)


def test_kit_engines_limits_profiles_and_engines():
    from kit.config import parse_engines

    assert parse_engines("") == ("n8n", "claude", "python")
    assert parse_engines("python, Claude") == ("claude", "python")
    assert parse_engines("n8n-agent,n8n") == ("n8n", "n8n-agent")  # the example only when listed
    assert compose_profiles({"KIT_ENGINES": "claude,python"}) == ["langfuse"]
    assert compose_profiles({"KIT_ENGINES": "n8n"}) == ["n8n", "langfuse"]
