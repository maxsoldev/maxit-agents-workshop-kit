"""Characterization tests at the runner seams (contracts §3-§6). They lock today's behaviour, so a refactor
of kit/runner must keep them green without edits. Fakes sit only at the outside seams: the n8n HTTP API,
the `claude` binary, the agent.py process, the OpenAI SDK and the Langfuse client.

Golden run files: tests/fixtures/golden/*.json. Rewrite them only on purpose: KIT_RECORD_GOLDEN=1.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

import httpx
import openai
import pytest

from kit import config, runner
from kit.runner import checks, engines, tracing

FIX = Path(__file__).parent / "fixtures"
GOLDEN = FIX / "golden"
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["category", "reply"],
    "properties": {
        "category": {"type": "string", "enum": ["a", "b"]},
        "reply": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "env": {"type": "object"},
    },
}
CASES = [
    {
        "id": "s01",
        "title": "Good",
        "today": "2026-11-04",
        "input": {"mode": "ok", "want": "a", "note_file": "data/note.txt"},
        "expected": {"category": "a"},
        "checks": [
            {"type": "equals", "field": "category"},
            {"type": "contains", "field": "reply", "value": "THANKS"},
            {"type": "judge", "field": "reply", "rubric": "Polite."},
        ],
    },
    {
        "id": "s02",
        "title": "Wrong",
        "input": {"mode": "ok", "want": "b"},
        "expected": {"category": "a"},
        "checks": [{"type": "equals", "field": "category"}, {"type": "judge", "field": "reply", "rubric": "Polite."}],
    },
    {"id": "s03", "title": "Bad schema", "input": {"mode": "bad"}, "expected": {}, "checks": []},
    {"id": "s04", "title": "Kept aside", "holdout": True, "input": {"mode": "ok", "want": "a"}, "checks": []},
]

PY_AGENT = r"""
import json, os, sys, time
case = json.load(sys.stdin)
inp = case["input"]
print("log line one", file=sys.stderr)
print('tool rules_get {"name": "routing"} -> "# Routing\\nok"', file=sys.stderr)
mode = inp.get("mode")
if mode == "crash":
    sys.exit("crashed on purpose")
if mode == "sleep":
    time.sleep(30)
if mode == "text":
    print("not json")
    sys.exit(0)
if mode == "list":
    print(json.dumps([{"category": "a", "reply": "Thanks"}]))
    sys.exit(0)
if mode == "number":
    print("5")
    sys.exit(0)
if mode == "env":
    keys = ["OPENAI_API_KEY", "KIT_MODEL", "KIT_TOOLS_URL", "OPENAI_BASE_URL", "TRACEPARENT"]
    print(json.dumps({"category": "a", "reply": "Thanks", "env": {k: os.environ.get(k) for k in keys}}))
    sys.exit(0)
if mode == "bad":
    print(json.dumps({"category": 5}))
    sys.exit(0)
print(json.dumps({"category": inp["want"], "reply": inp.get("note", "no note"), "tags": ["x"],
                  "env": {"today": case.get("today"), "has_schema": "output_schema" in case}}))
"""

CLAUDE_BIN = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv
prompt = args[args.index("-p") + 1]
case = json.loads(prompt.split("\n\n", 1)[1])
inp = case["input"]
schema = json.loads(args[args.index("--json-schema") + 1]) if "--json-schema" in args else None
mode = inp.get("mode")
def out(ev): print(json.dumps(ev))
print("claude stderr line", file=sys.stderr)
out({"type": "system", "subtype": "init"})
out({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t0", "name": "ToolSearch", "input": {"q": 1}},
    {"type": "tool_use", "id": "t1", "name": "mcp__kit__rules_get", "input": {"name": "routing"}}]}})
out({"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "# Routing"}]}]}})
if mode == "nothing":
    sys.exit(3)
if mode == "error":
    out({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "too many turns"})
    sys.exit(1)
answer = {"category": inp.get("want", "a"), "reply": inp.get("note", "hi"), "tags": ["c"]}
if mode == "env":
    keys = ["OPENAI_API_KEY", "KIT_MODEL", "KIT_TOOLS_URL", "TRACEPARENT", "OTEL_TRACES_EXPORTER",
            "CLAUDE_CODE_ENABLE_TELEMETRY", "OTEL_RESOURCE_ATTRIBUTES", "OTEL_LOG_TOOL_DETAILS"]
    answer["env"] = {k: os.environ.get(k) for k in keys}
    answer["env"]["argv"] = [a for a in args[1:] if a != prompt]
ev = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "total_cost_usd": 0.02,
      "duration_ms": 7, "modelUsage": {"claude-x": {}}}
if mode == "text":
    ev["result"] = "Sure:\n```json\n" + json.dumps(answer) + "\n```"
elif mode == "badtext":
    ev["result"] = '{"category": "a" "reply": "x"}'
elif mode == "bad":
    ev.update(result="{}", structured_output={"category": 5})
else:
    ev.update(result=json.dumps(answer), structured_output=answer)
out(ev)
"""


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def uc(tmp_path, monkeypatch):
    """A use case `seam` in a tmp usecases folder, a fake `claude` on PATH, runs in tmp, Langfuse off."""
    root = tmp_path / "usecases" / "seam"
    (root / "data").mkdir(parents=True)
    (root / "python").mkdir()
    (root / "claude").mkdir()
    (root / "data" / "note.txt").write_text("Thanks for the mail.\n", encoding="utf-8")
    (root / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in CASES), encoding="utf-8")
    (root / "output.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (root / "python" / "agent.py").write_text(PY_AGENT, encoding="utf-8")
    (root / "claude" / ".mcp.json").write_text("{}", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "claude", CLAUDE_BIN.replace("/usr/bin/env python3", sys.executable, 1))
    monkeypatch.setattr(config, "USECASES_DIR", tmp_path / "usecases")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for k, v in {
        "LANGFUSE": "off",
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_BASE_URL": "",
        "KIT_MODEL": "m-test",
        "KIT_JUDGE_MODEL": "j-test",
        "KIT_TOOLS_PORT": "5799",
        "N8N_API_KEY": "",
        "KIT_PARALLEL": "4",
    }.items():
        monkeypatch.setenv(k, v)
    return root


# ---------- fake OpenAI (the judge) ----------


class FakeOpenAI:
    """Stands in for `openai.OpenAI`. Scores 5 when the field value has "Thanks", else 2."""

    calls: list[dict] = []
    refuse_temperature = False

    def __init__(self, **kw):
        FakeOpenAI.calls.append({"client": kw})
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        FakeOpenAI.calls.append(kw)
        if FakeOpenAI.refuse_temperature and "temperature" in kw:
            req = httpx.Request("POST", "http://openai/v1/chat/completions")
            raise openai.BadRequestError(
                "Unsupported value: 'temperature'", response=httpx.Response(400, request=req), body=None
            )
        user = kw["messages"][1]["content"]
        value = user.split("Field value:\n", 1)[1]
        score = 5 if "Thanks" in value else 2
        content = (
            f'Here: {{"score": {score}, "reason": "r{score}"}}'
            if "TEXTY" in user
            else json.dumps({"score": score, "reason": f"r{score}"})
        )
        return NS(choices=[NS(message=NS(content=content))])


@pytest.fixture
def fake_openai(monkeypatch):
    FakeOpenAI.calls = []
    FakeOpenAI.refuse_temperature = False
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return FakeOpenAI


# ---------- fake n8n (webhook + public API) ----------


class N8nState:
    def __init__(self):
        self.hooks: dict[str, dict] = {}  # hook -> {"status", "body", "raw", "execution", "sleep"}
        self.calls: dict[str, list] = {}  # hook -> [(x-kit-call, case_id)] in POST order
        self.bodies: dict[str, dict] = {}
        self.version = {"versionId": "v1", "activeVersionId": "v1"}


def _executions(state: N8nState, hook: str) -> list[dict]:
    """One recorded execution per POST to `hook`, newest first, each with its own x-kit-call header."""
    spec = state.hooks.get(hook) or {}
    if not spec.get("execution"):
        return []
    found = []
    for call_id, case_id in reversed(state.calls.get(hook, [])):
        e = json.loads((FIX / spec["execution"]).read_text(encoding="utf-8"))
        case_in = e["data"]["resultData"]["runData"].setdefault("Case in", [{"data": {"main": [[{"json": {}}]]}}])
        case_in[0]["data"]["main"][0][0]["json"]["headers"] = {"x-kit-call": call_id}
        e["id"] = f"ex-{case_id}"
        found.append(e)
    return found


def make_handler(state: N8nState):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body, raw: bool = False):
            data = body.encode() if raw else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain" if raw else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            hook = self.path.removeprefix("/webhook/")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            spec = state.hooks.get(hook)
            if not self.path.startswith("/webhook/") or spec is None:
                return self._send(404, {"message": "not registered"})
            state.calls.setdefault(hook, []).append((self.headers.get("x-kit-call", ""), body.get("case_id")))
            state.bodies[hook] = body
            if spec.get("sleep"):
                time.sleep(spec["sleep"])
            if "raw" in spec:
                return self._send(spec.get("status", 200), spec["raw"], raw=True)
            answer = spec.get("body")
            if answer == "echo":
                inp = body["input"]
                answer = [{"category": inp.get("want", "a"), "reply": inp.get("note", "hi"), "tags": ["n"]}]
            return self._send(spec.get("status", 200), answer)

        def do_GET(self):
            url = urlparse(self.path)
            if self.headers.get("X-N8N-API-KEY") != "n8n-key":
                return self._send(401, {"message": "unauthorized"})
            if url.path == "/api/v1/workflows":
                data = [
                    {
                        "id": f"w-{hook}",
                        "name": f"Kit: {hook}",
                        "active": True,
                        **state.version,
                        "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": hook}}],
                    }
                    for hook in state.hooks
                ]
                return self._send(200, {"data": data})
            if url.path == "/api/v1/executions":
                wid = parse_qs(url.query)["workflowId"][0]
                hook = wid.removeprefix("w-")
                return self._send(200, {"data": _executions(state, hook)})
            return self._send(404, {})

        def log_message(self, *args):
            pass

    return Handler


STRICT = {  # n8n evals need an OpenAI-strict schema
    "type": "object",
    "required": ["category", "reply", "tags"],
    "additionalProperties": False,
    "properties": {k: SCHEMA["properties"][k] for k in ("category", "reply", "tags")},
}


@pytest.fixture
def n8n(uc, monkeypatch):
    (uc / "output.schema.json").write_text(json.dumps(STRICT), encoding="utf-8")
    state = N8nState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("N8N_URL", state.url)
    yield state
    server.shutdown()


# ---------- fake Langfuse ----------


class FakeObs:
    def __init__(self, log, name, parent=None, trace_id="tr1", id_="sp1"):
        self.log, self.name, self.parent = log, name, parent
        self.trace_id, self.id = trace_id, id_
        self._otel_span = NS(_start_time=0, set_attribute=lambda k, v: log.append(("attr", name, k)))

    def start_observation(self, name, as_type, **kw):
        child = FakeObs(self.log, name, self.name)
        kw.pop("metadata", None)
        self.log.append(("obs", self.name, name, as_type, json.dumps(kw, sort_keys=True, default=str)))
        return child

    def end(self, end_time=None):
        pass

    def update(self, **kw):
        self.log.append(("update", self.name, json.dumps(kw, sort_keys=True, default=str)))

    def score_trace(self, name, value, comment=None):
        self.log.append(("score", self.name, name, value))


class FakeLangfuse:
    def __init__(self):
        self.log: list = []
        self.items: dict = {}
        self.api = NS(projects=NS(get=lambda: NS(data=[NS(id="proj")])))
        self.flushed = 0

    @contextmanager
    def start_as_current_observation(self, name, input):
        self.log.append(("trace", name, sorted(input)))
        yield FakeObs(self.log, name, trace_id=f"trace-{name}", id_=f"span-{name}")

    def get_trace_url(self, trace_id):
        return f"http://lf/trace/{trace_id}"

    def get_dataset(self, name):
        raise RuntimeError("not found")

    def create_dataset(self, name, description=None):
        return NS(id=f"ds-{name}")

    def create_dataset_item(self, dataset_name, id, status, **kw):
        self.items[id] = status

    @staticmethod
    def create_trace_id(seed):
        return f"exp-{seed}"

    def flush(self):
        self.flushed += 1


@pytest.fixture
def langfuse(monkeypatch):
    fake = FakeLangfuse()
    monkeypatch.setenv("LANGFUSE", "local")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
    monkeypatch.setattr(tracing, "client", lambda: fake)
    monkeypatch.setattr(tracing, "_client", fake)
    return fake


# ---------- golden run files ----------


def normal(run: dict, n8n_url: str = "") -> dict:
    """The run with the parts that change on every run made fixed: ids, times, ports."""
    text = json.dumps(run, ensure_ascii=False, sort_keys=True)
    if n8n_url:
        text = text.replace(n8n_url, "http://N8N")
    text = text.replace(run["run_id"], "<run_id>")
    out = json.loads(text)
    out["started_at"] = out["ended_at"] = "<time>"
    for c in out["cases"]:
        assert isinstance(c["seconds"], float)
        c["seconds"] = "<s>"
    return out


def assert_golden(name: str, value) -> None:
    path = GOLDEN / f"{name}.json"
    if os.environ.get("KIT_RECORD_GOLDEN") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert json.loads(json.dumps(value)) == json.loads(path.read_text(encoding="utf-8")), f"differs from {path}"


def check_run_file(run: dict) -> None:
    assert re.fullmatch(r"\d{8}-\d{6}-seam-[a-z0-9-]+", run["run_id"])
    assert runner.load_run(run["run_id"]) == json.loads(json.dumps(run))  # the file has the same as the return
    row = runner.list_runs("seam")[0]
    assert row["run_id"] == run["run_id"] and row["case_ids"] == sorted(c["id"] for c in run["cases"])
    assert set(row) == {"run_id", "name", "usecase", "engine", "started_at", "ended_at", "summary"} | {
        "case_ids",
        "case_hashes",
    }


# ---------- S1: run_eval on each engine: the run file ----------


def test_python_run_file(uc, fake_openai):
    events = []
    run = runner.run_eval("seam", "python", name="py", on_progress=events.append)
    check_run_file(run)
    assert_golden("run-python", normal(run))
    assert sorted((e["case_id"], e["status"]) for e in events) == sorted(
        [(c, s) for c in ("s01", "s02", "s03") for s in ("running", "done")]
    )
    # the judge got the rubric, the input without *_raw, and the tool calls; temperature 0, the judge model
    judged = [c for c in fake_openai.calls if "messages" in c]
    assert {c["model"] for c in judged} == {"j-test"} and all(c["temperature"] == 0 for c in judged)
    assert any("Tool calls of the agent" in c["messages"][1]["content"] for c in judged)


def test_claude_run_file(uc, fake_openai):
    run = runner.run_eval("seam", "claude", case_ids=["s01", "s02", "s03"])
    check_run_file(run)
    assert_golden("run-claude", normal(run))


def test_n8n_run_file_with_execution_data(uc, n8n, fake_openai, monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "n8n-key")
    n8n.hooks["seam"] = {"body": "echo", "execution": "n8n_workflow_execution.json"}
    run = runner.run_eval("seam", "n8n", case_ids=["s01", "s02"], name="n")
    check_run_file(run)
    assert n8n.bodies["seam"]["output_schema"] == STRICT and n8n.bodies["seam"]["case_id"] in ("s01", "s02")
    assert "n8n_nodes" not in json.dumps(run)  # the nodes go to the trace, not to the run file
    assert_golden("run-n8n", normal(run, n8n.url))


def test_n8n_without_api_key_has_no_links_and_no_tool_calls(uc, n8n, fake_openai):
    n8n.hooks["seam"] = {"body": "echo", "execution": "n8n_workflow_execution.json"}
    case = runner.run_eval("seam", "n8n", case_ids=["s01"])["cases"][0]
    assert case["n8n_execution_url"] is None and case["meta"]["tool_calls"] == [] and case["passed"]
    assert runner.n8n_warning("seam") is None


# ---------- S1: error paths, as the run file shows them ----------


def one_case(uc_root: Path, engine: str, case: dict) -> dict:
    """Run one ad-hoc case through run_eval (written to cases.jsonl) and return its result."""
    (uc_root / "cases.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
    return runner.run_eval("seam", engine)["cases"][0]


@pytest.mark.parametrize(
    "mode, error",
    [
        (
            "crash",
            'agent.py exit 1: log line one\ntool rules_get {"name": "routing"} -> "# Routing\\nok"\ncrashed on purpose',
        ),
        ("text", "agent.py stdout is not JSON: 'not json\\n'. Print only the result; write logs to stderr"),
        ("number", "the result must be one JSON object, got int"),
        ("list", None),
    ],
)
def test_python_errors(uc, mode, error):
    res = one_case(uc, "python", {"id": "e", "input": {"mode": mode}})
    assert res["error"] == error
    assert res["meta"]["stderr"].startswith("log line one") and res["meta"]["tool_calls"][0]["name"] == "rules_get"
    if mode == "list":
        assert res["output"] == {"category": "a", "reply": "Thanks"} and res["passed"]


def test_python_timeout_and_missing_agent(uc, monkeypatch):
    monkeypatch.setitem(engines.TIMEOUTS, "python", 1)
    t0 = time.time()
    res = one_case(uc, "python", {"id": "e", "input": {"mode": "sleep"}})
    assert res["error"] == "agent.py did not finish in 1 s" and time.time() - t0 < 8
    (uc / "python" / "agent.py").unlink()
    assert one_case(uc, "python", {"id": "e", "input": {}})["error"] == "usecases/seam/python/agent.py not found"


@pytest.mark.parametrize(
    "mode, error",
    [
        ("nothing", "claude exit 3, no JSON answer: claude stderr line (is Claude logged in? run `claude`)"),
        ("error", "Claude failed (error_max_turns): too many turns"),
        ("text", None),
    ],
)
def test_claude_errors(uc, mode, error):
    res = one_case(uc, "claude", {"id": "e", "input": {"mode": mode}})
    assert res["error"] == error
    if mode == "text":  # no structured_output: the JSON comes from the text
        assert res["output"]["tags"] == ["c"]


def test_claude_bad_json_text_and_missing_parts(uc, monkeypatch):
    schema_off = {"id": "e", "input": {"mode": "badtext"}}
    (uc / "output.schema.json").write_text("{}", encoding="utf-8")  # no schema: Claude answers text
    err = one_case(uc, "claude", schema_off)["error"]
    assert err.startswith("Claude's answer has no valid JSON (Expecting ',' delimiter near ")
    assert err.endswith("Tell it in CLAUDE.md to answer only JSON")
    (uc / "claude" / ".mcp.json").unlink()
    err = one_case(uc, "claude", schema_off)["error"]
    assert err == "usecases/seam/claude/.mcp.json not found: the Claude agent folder needs it"
    monkeypatch.setenv("PATH", "/nonexistent")
    err = one_case(uc, "claude", schema_off)["error"]
    assert err == "`claude` not found on PATH: install Claude Code, then run `claude` once to log in"


def test_claude_gets_the_schema_and_the_flags(uc):
    res = one_case(uc, "claude", {"id": "e", "input": {"mode": "env"}})
    argv = res["output"]["env"]["argv"]
    assert argv[:1] == ["-p"] and "--json-schema" in argv
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert "$schema" not in schema and schema["required"] == ["category", "reply"]
    assert argv[argv.index("--allowedTools") + 1] == "mcp__kit" and "--no-session-persistence" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert res["meta"]["tool_calls"] == [{"name": "rules_get", "input": {"name": "routing"}, "output": "# Routing"}]
    assert res["meta"]["models"] == ["claude-x"] and res["meta"]["stderr"] == "claude stderr line"


def test_engine_env(uc, monkeypatch):
    env = one_case(uc, "python", {"id": "e", "input": {"mode": "env"}})["output"]["env"]
    assert env == {
        "OPENAI_API_KEY": "sk-test",
        "KIT_MODEL": "m-test",
        "KIT_TOOLS_URL": "http://localhost:5799/mcp",
        "OPENAI_BASE_URL": None,  # empty in .env: not passed at all
        "TRACEPARENT": None,
    }
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gw:4000/v1/")
    env = one_case(uc, "python", {"id": "e", "input": {"mode": "env"}})["output"]["env"]
    assert env["OPENAI_BASE_URL"] == "http://gw:4000/v1"


@pytest.mark.parametrize(
    "spec, error",
    [
        (
            {"status": 500, "body": {"message": "Error in workflow"}},
            'n8n answered 500: {"message": "Error in workflow"}',
        ),
        ({"status": 200, "raw": "hello"}, "n8n answered text, not JSON: 'hello'. End the workflow with Respond to"),
        ({"status": 200, "body": [1, 2]}, "the result must be one JSON object, got list"),
    ],
)
def test_n8n_errors(uc, n8n, spec, error):
    n8n.hooks["seam"] = spec
    res = one_case(uc, "n8n", {"id": "e", "input": {}})
    assert res["error"].startswith(error) and res["output"] is None


def test_n8n_404_unreachable_and_timeout(uc, n8n, monkeypatch):
    res = one_case(uc, "n8n", {"id": "e", "input": {}})
    assert res["error"] == (
        "n8n answered 404 for /webhook/seam: is the workflow active and is its webhook path 'seam'? run `make setup`"
    )
    n8n.hooks["seam"] = {"body": "echo", "sleep": 3}
    monkeypatch.setitem(engines.TIMEOUTS, "n8n", 1)
    res = one_case(uc, "n8n", {"id": "e", "input": {}})
    assert res["error"] == "n8n did not answer in 1 s: look at the execution in n8n (Executions)"
    monkeypatch.setenv("N8N_URL", "http://127.0.0.1:9")
    res = one_case(uc, "n8n", {"id": "e", "input": {}})
    assert res["error"] == (
        "cannot reach n8n at http://127.0.0.1:9: is it running? run `make up` (or check N8N_URL in .env)"
    )


def test_n8n_failure_names_the_node_and_links_the_execution(uc, n8n, monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "n8n-key")
    n8n.hooks["seam"] = {
        "status": 500,
        "body": {"message": "Error in workflow"},
        "execution": "n8n_parser_error_execution.json",
    }
    res = one_case(uc, "n8n", {"id": "e", "input": {}})
    link = f"{n8n.url}/workflow/w-seam/executions/ex-e"
    assert res["error"] == (
        "n8n failed at Triage > Triage fields: The AI model returned an empty response to the Structured "
        f"Output Parser (Model output wrapper is an empty object) (open the n8n execution: {link})"
    )
    assert res["n8n_execution_url"] == link


def test_hard_deadline(uc, monkeypatch):
    monkeypatch.setitem(engines.TIMEOUTS, "python", 1)
    monkeypatch.setattr(engines, "GRACE_S", 0.5)
    real = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: time.sleep(5) if "agent.py" in cmd else real(cmd, **k))
    t0 = time.time()
    res = one_case(uc, "python", {"id": "e", "input": {}})
    assert time.time() - t0 < 4
    assert res["error"] == "python did not finish in 1 s (hard deadline): the runner stopped waiting"


def test_run_eval_refuses_to_start(uc):
    with pytest.raises(ValueError, match="unknown engine 'zzz'; use one of n8n, claude, python"):
        runner.run_eval("seam", "zzz")
    with pytest.raises(ValueError, match="use case 'nope' not found in usecases/"):
        runner.run_eval("nope", "python")
    with pytest.raises(ValueError, match="unknown case id\\(s\\): x, y"):
        runner.run_eval("seam", "python", case_ids=["x", "s01", "y"])
    (uc / "output.schema.json").write_text('{"type": 5}', encoding="utf-8")
    with pytest.raises(runner.CaseError, match="output.schema.json is not a valid JSON Schema: 5 is not valid"):
        runner.run_eval("seam", "python")
    (uc / "output.schema.json").unlink()
    with pytest.raises(runner.CaseError, match="usecases/seam/output.schema.json not found"):
        runner.run_eval("seam", "python")


# ---------- S2: checks ----------

OUT = {
    "category": "a",
    "reply": "Thanks, Elena",
    "empty": "",
    "tags": ["vip", "x"],
    "n": 3,
    "options": [{"price": 10, "trains": ["ICE 1"]}, {"price": 20, "trains": []}],
    "tasks": [{"kind": "trip", "who": {"name": "Elena Popescu"}, "n": 1, "ok": True}],
}
EXPECTED = {"category": "b", "options": [{"price": 10}, {"price": 21}]}


@pytest.mark.parametrize(
    "check, passed, reason",
    [
        ({"type": "equals", "field": "category"}, False, "got a, expected b"),
        ({"type": "equals", "field": "category", "value": "a"}, True, "got a"),
        ({"type": "equals", "field": "n"}, False, "no expected `n` in the case and no `value` in the check"),
        ({"type": "equals", "field": "empty", "value": "x"}, False, 'got "" (empty), expected x'),
        ({"type": "equals", "field": "options.0.price", "value": 10}, True, "got 10"),
        (
            {"type": "equals", "field": "options.9.price", "value": 10},
            False,
            "`options.9.price` is missing in the result",
        ),
        ({"type": "equals", "field": "options.*.price"}, False, "item 2 of 2: got 20, expected 21"),
        ({"type": "in", "field": "category", "values": ["a", "c"]}, True, "got a"),
        ({"type": "in", "field": "category", "values": ["c"]}, False, 'got a, expected one of ["c"]'),
        ({"type": "contains", "field": "reply", "value": "elena"}, True, "has elena"),
        ({"type": "contains", "field": "tags", "value": "VIP"}, False, "does not have VIP"),
        ({"type": "contains", "field": "n", "value": 3}, False, "does not have 3"),
        ({"type": "not_contains", "field": "reply", "value": "@"}, True, "does not have @"),
        (
            {"type": "not_contains", "field": "nope", "value": "@"},
            True,
            "`nope` is missing or empty, so it does not have @",
        ),
        ({"type": "not_contains", "field": "nope.*.x", "value": "@"}, True, "`nope` is missing, so it does not have @"),
        ({"type": "count", "field": "tags", "equals": 2}, True, "2 items"),
        ({"type": "count", "field": "tags", "min": 3}, False, "2 items, expected min 3"),
        ({"type": "count", "field": "reply", "max": 3}, False, "`reply` is not a list: Thanks, Elena"),
        ({"type": "count", "field": "options.*.trains", "max": 0}, False, "item 1 of 2: 1 items, expected max 0"),
        (
            {"type": "any", "field": "tasks", "where": {"who.name": "elena"}},
            True,
            'item 1 of 1 matches {"who.name": "elena"}',
        ),
        (
            {"type": "any", "field": "tasks", "where": {"n": 2}},
            False,
            'no item of 1 matches {"n": 2}; closest is item 1: `n` is 1',
        ),
        (
            {"type": "any", "field": "tasks", "where": {"ok": True, "zz": 1}},
            False,
            'no item of 1 matches {"ok": true, "zz": 1}; closest is item 1: `zz` is missing',
        ),
        ({"type": "any", "field": "tasks", "contains": {"kind": "TRI"}}, True, 'item 1 of 1 matches {"kind": "~TRI"}'),
        (
            {"type": "any", "field": "options.1.trains", "where": {"a": 1}},
            False,
            '`options.1.trains` is empty, so no item matches {"a": 1}',
        ),
        ({"type": "any", "field": "reply", "where": {"a": 1}}, False, "`reply` is not a list: Thanks, Elena"),
        ({"type": "magic", "field": "reply"}, False, "unknown check type 'magic'"),
        ({"type": "contains", "field": "empty.*", "value": "x"}, False, '`empty` is not a list: "" (empty)'),
        ({"type": "contains", "field": "options.1.trains.*", "value": "x"}, True, "all 0 items pass"),
    ],
)
def test_check_types(check, passed, reason):
    r = checks.run_check(check, OUT, EXPECTED, {})
    assert (r["passed"], r["reason"]) == (passed, reason)
    assert (r["type"], r["field"]) == (check["type"], check["field"])


def test_checks_on_no_output_and_def_kept(uc):
    case = {"checks": [{"type": "equals", "field": "a"}, {"type": "not_contains", "field": "b", "value": "x"}]}
    rs = checks.run_checks(case, None, SCHEMA, {})
    assert [(r["type"], r["passed"], r["reason"]) for r in rs] == [
        ("schema", False, "no output"),
        ("equals", False, "no output"),
        ("not_contains", False, "no output"),
    ]
    assert "def" not in rs[0] and [r["def"] for r in rs[1:]] == case["checks"]


def test_judge_check(uc, fake_openai):
    run = lambda value, **kw: checks.run_check(  # noqa: E731
        {"type": "judge", "field": "r", "rubric": "Polite.", **kw}, {"r": value}, {}, {"email": "x", "email_raw": "RAW"}
    )
    assert run("Thanks") == {
        "type": "judge",
        "field": "r",
        "passed": True,
        "reason": "score 5/5 (min 4): r5",
        "score": 5,
    }
    assert run("No.", min=2)["passed"] and not run("No.")["passed"]
    user = fake_openai.calls[-1]["messages"][1]["content"]
    assert "RAW" not in user and user.startswith("Rubric:\nPolite.\n\nCase input:\n")
    assert run("")["reason"] == "score 2/5 (min 4): r2"
    assert "(empty: the agent wrote nothing in this field)" in fake_openai.calls[-1]["messages"][1]["content"]
    assert run("TEXTY Thanks")["reason"] == "score 5/5 (min 4): r5"  # text before the JSON
    fake_openai.refuse_temperature = True  # a reasoning model: retry without temperature
    assert run("Thanks")["passed"] and "temperature" not in fake_openai.calls[-1]
    client = next(c["client"] for c in fake_openai.calls if "client" in c)
    assert client == {"api_key": "sk-test", "base_url": None, "timeout": 45, "max_retries": 1}


def test_judge_skips(uc, fake_openai, monkeypatch):
    star = {"type": "judge", "field": "xs.*", "rubric": "r"}
    monkeypatch.setenv("OPENAI_API_KEY", "")
    r = checks.run_check({"type": "judge", "field": "r", "rubric": "r"}, {"r": "Thanks"}, {}, {})
    assert r == {
        "type": "judge",
        "field": "r",
        "passed": None,
        "reason": "judge skipped: no OPENAI_API_KEY: set it in .env to run judge checks",
        "skipped": True,
    }
    assert checks.run_check(star, {"xs": ["a"]}, {}, {})["skipped"]
    run = runner.run_eval("seam", "python", case_ids=["s01"])
    assert run["summary"]["skipped"] == {"judge": 1} and run["cases"][0]["passed"]


# ---------- S4: tracing (fake Langfuse) ----------


def test_traces_scores_and_tool_spans(uc, langfuse, fake_openai):
    run = runner.run_eval("seam", "python", case_ids=["s01", "s02"], name="lf")
    assert run["experiment_url"] == f"http://lf/project/proj/experiments/results?baseline=exp-{run['run_id']}"
    assert langfuse.items == {f"seam-{c['id']}": "ACTIVE" for c in CASES}
    assert run["cases"][0]["trace_url"] == "http://lf/trace/trace-seam/s01"
    log = [x for x in langfuse.log if x[1] == "seam/s01" or x[0] == "trace" and x[1] == "seam/s01"]
    assert ("trace", "seam/s01", ["case_id", "input", "output_schema", "today"]) in log
    assert ("score", "seam/s01", "equals.category", 1) in log and ("score", "seam/s01", "passed", 1) in log
    assert ("score", "seam/s01", "schema", 1) in log and ("score", "seam/s01", "judge.reply", 5) in log
    # a judge score is its 1-5 score (the compare view shows 2, not 0); `passed` stays 1/0
    assert ("score", "seam/s02", "judge.reply", 2) in langfuse.log and (
        "score",
        "seam/s02",
        "passed",
        0,
    ) in langfuse.log
    tools = [x for x in log if x[0] == "obs"]
    assert [(x[2], x[3]) for x in tools] == [("tool:rules_get", "tool")]
    attrs = [x[2] for x in langfuse.log if x[0] == "attr" and x[1] == "seam/s01"]
    assert len(attrs) == 1  # the experiment's expected output, on the case root span
    assert langfuse.flushed >= 1


def test_trace_of_a_failed_case_and_claude_env(uc, langfuse):
    res = one_case(uc, "claude", {"id": "e", "input": {"mode": "env"}, "checks": []})
    env = res["output"]["env"]
    assert env["TRACEPARENT"] == "00-trace-seam/e-span-seam/e-01" and env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert (
        env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
        and env["OTEL_RESOURCE_ATTRIBUTES"] == "service.name=claude-code-seam"
    )
    res = one_case(uc, "python", {"id": "f", "input": {"mode": "crash"}, "checks": []})
    upd = [json.loads(x[2]) for x in langfuse.log if x[0] == "update" and x[1] == "seam/f"]
    assert upd == [{"level": "ERROR", "output": {"error": res["error"], "output": None}}]
    assert ("score", "seam/f", "passed", 0) in langfuse.log
    env = one_case(uc, "python", {"id": "g", "input": {"mode": "env"}})["output"]["env"]
    assert env["TRACEPARENT"] is None  # only Claude gets the OTel env


def test_n8n_node_observations(uc, n8n, langfuse, monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "n8n-key")
    n8n.hooks["seam"] = {"body": "echo", "execution": "n8n_workflow_execution.json"}
    one_case(uc, "n8n", {"id": "e", "input": {"want": "a"}, "checks": []})
    call_id = n8n.calls["seam"][0][0]
    obs = [(x[1], x[2], x[3], json.loads(x[4].replace(call_id, "<call>"))) for x in langfuse.log if x[0] == "obs"]
    assert_golden("trace-n8n-observations", obs)


def test_check_run_makes_no_experiment_and_run_case_makes_no_file(uc, langfuse, capsys):
    run = runner.run_eval("seam", "python", case_ids=["s03"], name="check")
    assert run["experiment_url"] is None and langfuse.items == {}
    before = sorted(p.name for p in config.RUNS_DIR.glob("*.json"))
    from kit.runner import cli

    assert cli.run_one("seam", "python", "s01") == 0
    assert sorted(p.name for p in config.RUNS_DIR.glob("*.json")) == before
    out = capsys.readouterr().out
    assert "===== Engine input (plus output_schema) =====" in out and "===== Tool call rules_get =====" in out
    assert "===== Checks =====" in out and "\nPASS  " in out or "\nFAIL  " in out
