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

    def n8n_warning(uc):
        return "n8n has an older 'Kit: demo workflow' than the kit: run `make setup`."

    monkeypatch.setitem(sys.modules, "kit.runner", types.SimpleNamespace(run_eval=run_eval, n8n_warning=n8n_warning))
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
    assert lines[1] == "WARN     demo/n8n: n8n has an older 'Kit: demo workflow' than the kit: run `make setup`."
    assert lines[2] == "Running 2 checks in parallel (one case each; this can take a few minutes) ..."
    assert lines[3].startswith("ERROR    demo/n8n (d1): HTTP 404. Next: ")  # fixed order: n8n, claude, python
    assert lines[4].startswith("SKIP     demo/claude (d1)")
    # a wrong answer is not an error; a skipped judge does not count
    assert lines[5] == "ANSWERS  demo/python (d1): 1/2 checks pass. equals who: got Ann, expected Bob"
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
        "name": "Kit: marketing workflow",
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
    # all pins go (Debug in editor pins every node; an export must not save that); pins are not part of the hash
    pins = {"Case in": [{"json": {"body": {"case_id": "m14"}}}], "Triage": [{"json": {"text": "old answer"}}]}
    body = workflow_body({**wf, "pinData": pins})
    assert "pinData" not in body and same_workflow({**body, "pinData": pins}, workflow_body(wf))


def test_setup_removes_pins_from_an_unchanged_workflow(tmp_path, monkeypatch):
    """The n8n copy is the same as the file but has pinned data (an old kit file, or Debug in editor):
    setup updates it with pinData {} (without the key, n8n keeps the pins)."""
    wf = {"name": "Kit: x workflow", "nodes": [{"name": "Case in", "type": "webhook", "parameters": {}}]}
    (tmp_path / "usecases/x/n8n").mkdir(parents=True)
    (tmp_path / "usecases/x/n8n/workflow.json").write_text(json.dumps(wf))
    for name, value in [("ROOT", tmp_path), ("RUNS_DIR", tmp_path / "runs"), ("USECASES_DIR", tmp_path / "usecases")]:
        monkeypatch.setattr(setup, name, value)
    monkeypatch.setattr(setup, "IMPORTED_FILE", tmp_path / "runs/n8n-imported.json")
    monkeypatch.setattr(setup, "free_webhook_paths", lambda *a: None)
    calls = []
    stored = {**workflow_body(wf), "id": "w1", "active": True, "pinData": {"Case in": [{"json": {}}]}}

    def api(method, path, body=None):
        calls.append((method, path, body))
        return httpx.Response(200, json=stored if method == "GET" else {})

    fake = types.SimpleNamespace(workflows=lambda: [stored], api=api)
    assert setup.import_workflows(fake, "cred")
    puts = [b for m, _, b in calls if m == "PUT"]
    assert len(puts) == 1 and puts[0]["pinData"] == {}
    calls.clear()
    stored.pop("pinData")
    assert setup.import_workflows(fake, "cred")
    assert not [c for c in calls if c[0] == "PUT"]  # no pins: unchanged, no update


def test_push_deletes_only_the_old_agent_examples(tmp_path, monkeypatch, capsys):
    """`Kit: <uc> agent` and `Kit: <uc> agent (example)` go (active or not), each after a backup.
    Other workflows stay, also a team's own agent."""
    wf = {"name": "Kit: x workflow", "nodes": [{"name": "Case in", "type": "webhook", "parameters": {}}]}
    (tmp_path / "usecases/x/n8n").mkdir(parents=True)
    (tmp_path / "usecases/x/n8n/workflow.json").write_text(json.dumps(wf))
    for name, value in [("ROOT", tmp_path), ("RUNS_DIR", tmp_path / "runs"), ("USECASES_DIR", tmp_path / "usecases")]:
        monkeypatch.setattr(setup, name, value)
    monkeypatch.setattr(setup, "IMPORTED_FILE", tmp_path / "runs/n8n-imported.json")
    monkeypatch.setattr(setup, "free_webhook_paths", lambda *a: None)
    monkeypatch.setattr(setup, "usecases", lambda: ["x"])
    flows = [
        {**workflow_body(wf), "id": "w1", "active": True},
        {"name": "Kit: x agent", "id": "a1", "active": True, "nodes": []},
        {"name": "Kit: x agent (example)", "id": "a2", "active": False, "nodes": []},
        {"name": "Kit: x agent v2", "id": "t1", "active": True, "nodes": []},
        {"name": "Kit: y agent", "id": "t2", "active": False, "nodes": []},  # not a use case of this kit
    ]
    setup.remember_import("Kit: x workflow", workflow_body(wf))
    calls = []

    def api(method, path, body=None):
        calls.append((method, path))
        if (method, path) == ("DELETE", "/workflows/a1") and calls.count((method, path)) == 1:
            return httpx.Response(409, json={"message": "Workflow is still being unpublished."})
        wid = path.split("/")[2] if path.count("/") >= 2 else ""
        return httpx.Response(200, json=next((w for w in flows if w["id"] == wid), {}))

    monkeypatch.setattr(setup.time, "sleep", lambda s: None)
    fake = types.SimpleNamespace(workflows=lambda: flows, api=api)
    assert setup.import_workflows(fake, "cred")
    assert [p for m, p in calls if m == "DELETE"] == ["/workflows/a1"] * 2 + ["/workflows/a2"]  # a 409, then again
    assert ("POST", "/workflows/a1/deactivate") in calls and ("POST", "/workflows/a2/deactivate") not in calls
    backups = sorted(p.name for p in (tmp_path / "runs/n8n-backup").iterdir())
    assert [b.rsplit("-", 2)[0] for b in backups] == ["deleted-kit_x_agent", "deleted-kit_x_agent_example"]
    assert json.loads((tmp_path / "runs/n8n-backup" / backups[0]).read_text())["id"] == "a1"
    out = capsys.readouterr().out
    assert "Kit: x agent: old example deleted (copy: runs/n8n-backup/deleted-kit_x_agent-" in out


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
    assert parse_engines("n8n-agent,n8n") == ("n8n",)  # the old example engine is gone
    assert compose_profiles({"KIT_ENGINES": "claude,python"}) == ["langfuse"]
    assert compose_profiles({"KIT_ENGINES": "n8n"}) == ["n8n", "langfuse"]


def test_workflow_files_round_trip():
    """Each use case has one n8n workflow file, with no prompt text in it: an AI node gets its prompt from its
    business rules file (n8n/<step>.md) through Read the rules. A pull with no change keeps git diff clean."""
    from kit.config import USECASES_DIR

    files = sorted(USECASES_DIR.glob("*/n8n/*.json"))
    assert [f.name for f in files] == ["workflow.json"] * len(files)  # one n8n workflow per use case
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "{{PROMPT:" not in text and not (f.parent / "prompts").exists()
        assert json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n" == text, f


def test_sync_state():
    assert setup.sync_state("a", "a", None) == "same"
    assert setup.sync_state("a", "b", "a") == "n8n"  # edited in n8n
    assert setup.sync_state("b", "a", "a") == "files"  # edited in the files (or git pull)
    assert setup.sync_state("b", "c", "a") == "both"
    assert setup.sync_state("b", "c", None) == "unknown"


def test_push_keeps_n8n_edits_and_discards_drafts(tmp_path, monkeypatch):
    """Push: the files changed -> update n8n. n8n changed since the last sync -> keep it (unless FORCE).
    Only an unpublished draft -> discard it."""

    def wf(text):
        node = {"name": "Triage", "type": "x.chainLlm", "parameters": {"text": text}}
        return {"name": "Kit: x workflow", "nodes": [node], "connections": {}, "settings": {}}

    (tmp_path / "usecases/x/n8n").mkdir(parents=True)
    path = tmp_path / "usecases/x/n8n/workflow.json"
    path.write_text(json.dumps(wf("=v1")))
    for name, value in [("ROOT", tmp_path), ("RUNS_DIR", tmp_path / "runs"), ("USECASES_DIR", tmp_path / "usecases")]:
        monkeypatch.setattr(setup, name, value)
    monkeypatch.setattr(setup, "IMPORTED_FILE", tmp_path / "runs/n8n-imported.json")
    monkeypatch.setattr(setup, "free_webhook_paths", lambda *a: None)
    setup.remember_import("Kit: x workflow", wf("=v1"))
    calls, n8n = [], {"full": {**wf("=v1"), "id": "w1", "active": True}}

    def api(method, path, body=None):
        calls.append((method, body))
        return httpx.Response(200, json=n8n["full"] if method == "GET" else {})

    fake = types.SimpleNamespace(workflows=lambda: [n8n["full"]], api=api)
    puts = lambda: [b for m, b in calls if m == "PUT"]  # noqa: E731
    path.write_text(json.dumps(wf("=v2")))  # a file edit
    assert setup.import_workflows(fake, "cred") and puts()[0]["nodes"][0]["parameters"]["text"] == "=v2"
    calls.clear()
    n8n["full"] = {**wf("=mine"), "id": "w1", "active": True}  # edited and published in n8n
    assert setup.import_workflows(fake, "cred") and not puts()  # kept
    assert setup.import_workflows(fake, "cred", force=True) and puts()  # FORCE: the files win
    calls.clear()
    published = {"nodes": wf("=v2")["nodes"], "connections": {}}
    n8n["full"] = {**wf("=draft"), "id": "w1", "active": True, "activeVersionId": "a", "versionId": "b",
                   "activeVersion": published}  # fmt: skip
    assert setup.import_workflows(fake, "cred") and puts()  # an unpublished draft is discarded


def test_strict_schema_for_n8n():
    from kit.runner.engines.n8n import strict_problem

    item = {"type": "object", "additionalProperties": False, "required": ["c"], "properties": {"c": {}}}
    ok = {"type": "object", "additionalProperties": False, "required": ["a", "b"],
          "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": item}}}  # fmt: skip
    assert strict_problem(ok) is None
    assert strict_problem({**ok, "required": ["a"]}) == "the result: b not in required"
    inner = json.loads(json.dumps(ok))
    inner["properties"]["b"]["items"].pop("additionalProperties")
    assert strict_problem(inner) == "b: no additionalProperties: false"
    from kit.config import USECASES_DIR

    for f in USECASES_DIR.glob("*/output.schema.json"):  # the scaffolds work on n8n
        assert strict_problem(json.loads(f.read_text())) is None, f


def test_claude_engine_does_not_load_the_builder_files():
    """The builder's root CLAUDE.md and .claude/skills are for your Claude Code session, not for the agent."""
    from kit.config import ROOT, USECASES_DIR
    from kit.runner.engines.claude import builder_excludes

    out = builder_excludes(USECASES_DIR / "marketing" / "claude")
    assert str(ROOT / "CLAUDE.md") in out["claudeMdExcludes"]
    assert str(USECASES_DIR / "marketing" / "claude" / "CLAUDE.md") not in out["claudeMdExcludes"]
    builder = {p.name for p in (ROOT / ".claude" / "skills").glob("*/")}
    assert set(out["skillOverrides"]) == builder and set(out["skillOverrides"].values()) <= {"off"}


def test_credential_is_reused_from_the_kit_workflows(tmp_path, monkeypatch):
    """A new .env (no N8N_OPENAI_CREDENTIAL_ID) on an n8n that has the kit workflows: setup takes their
    credential, so the workflows stay the same as the files. No second credential."""
    s = types.SimpleNamespace(
        openai_api_key="sk-x", openai_base_url_docker="https://api.openai.com/v1", n8n_openai_credential_id=""
    )
    monkeypatch.setattr(setup, "settings", lambda: s)
    monkeypatch.setattr(setup, "ENV_FILE", tmp_path / ".env")
    cred = {"openAiApi": {"id": "old1", "name": setup.OPENAI_CRED_NAME}}
    flows = [{"name": "Kit: x workflow", "nodes": [{"name": "Model", "credentials": cred}]}]
    calls = []

    def api(method, path, body=None):
        calls.append((method, path))
        return httpx.Response(200, json={"id": "new1"})

    fake = types.SimpleNamespace(workflows=lambda: flows, api=api)
    assert setup.setup_credential(fake) == "old1"
    assert ("PATCH", "/credentials/old1") in calls and ("POST", "/credentials") not in calls
    assert setup.read_env(tmp_path / ".env")["N8N_OPENAI_CREDENTIAL_ID"] == "old1"
