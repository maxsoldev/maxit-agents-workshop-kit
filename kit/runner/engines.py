"""Call one engine with one case (contracts §4).

Each engine function returns `(output, error, meta)`:
output = the result dict (or None), error = a text that says what to do (or None), meta = extra facts (cost, turns).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid

import httpx

from kit import config
from kit.config import settings

TIMEOUTS = {"n8n": 120, "claude": 300, "python": 300}
GRACE_S = 10  # the hard deadline of a case = engine timeout + this
CLAUDE_PROMPT = "Process this case. Answer with only the result JSON.\n\n"


class Deadline(Exception):
    """`with_deadline` stopped waiting."""


def with_deadline(fn, seconds: float):
    """Return fn(), but wait at most `seconds` (wall clock); else raise Deadline.

    fn runs in a daemon thread: if it hangs, the thread stays, but the case goes on.
    """
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise Deadline(f"no answer in {seconds:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def call_with_deadline(uc: str, engine: str, engine_input: dict, extra_env: dict | None = None):
    """Like `call`, but never waits longer than the engine timeout + GRACE_S.
    The subprocess engines also kill their own process group at the timeout."""
    limit = TIMEOUTS.get(engine, 120)
    try:
        return with_deadline(lambda: call(uc, engine, engine_input, extra_env), limit + GRACE_S)
    except Deadline:
        return None, f"{engine} did not finish in {limit} s (hard deadline): the runner stopped waiting", {}


def call(uc: str, engine: str, engine_input: dict, extra_env: dict | None = None):
    """Run `engine` on one case. `extra_env` adds env variables (tracing)."""
    if engine == "n8n":
        return call_n8n(uc, engine_input)
    if engine == "claude":
        return call_claude(uc, engine_input, extra_env or {})
    if engine == "python":
        return call_python(uc, engine_input, extra_env or {})
    return None, f"unknown engine {engine!r}; use one of {', '.join(config.ENGINES)}", {}


def engine_env(extra: dict | None = None) -> dict:
    """The env for engine processes: the user's env plus the kit settings."""
    s = settings()
    env = dict(os.environ)
    env.update({"OPENAI_API_KEY": s.openai_api_key, "KIT_MODEL": s.model, "KIT_TOOLS_URL": s.tools_url})
    if s.openai_base_url:
        env["OPENAI_BASE_URL"] = s.openai_base_url  # the OpenAI SDK reads it
    else:
        env.pop("OPENAI_BASE_URL", None)  # an empty value makes the SDK call "" (APIConnectionError)
    env.update(extra or {})
    return env


def parse_json_text(text: str):
    """Find the result JSON in a text: the last fenced code block, else the whole text."""
    blocks = re.findall(r"```(?:json)?[ \t]*\n(.*?)```", text, re.S)
    candidate = blocks[-1] if blocks else text
    return json.loads(candidate.strip())


def json_problem(e: ValueError) -> str:
    """Where the JSON broke: `Expecting ',' delimiter near '...reasons."<br>, "route_to"'`."""
    if isinstance(e, json.JSONDecodeError):
        return f"{e.msg} near {e.doc[max(0, e.pos - 60) : e.pos + 20]!r}"
    return str(e)


def _as_result(value):
    """n8n can answer `[{...}]`: take the one object. Return (dict, None) or (None, error)."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        return None, f"the result must be one JSON object, got {type(value).__name__}"
    return value, None


def _run(cmd: list[str], cwd, stdin: str, env: dict, timeout: int):
    """Run a command; on timeout kill it and its children. Return (code, stdout, stderr), code None = timeout."""
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = p.communicate(stdin, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:  # a child outside the group can keep the pipes open: do not wait for it forever
            out, err = p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = "", ""
        return None, out, err


def _tail(text: str, lines: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def call_n8n(uc: str, engine_input: dict):
    """POST the case to the webhook. meta["execution_url"] links to the n8n execution (if found)."""
    call_id = uuid.uuid4().hex
    output, error, meta = _post_n8n(uc, engine_input, call_id)
    if meta.get("status"):  # n8n answered, so there is an execution
        try:
            meta["execution_url"] = n8n_execution_url(uc, call_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            meta["execution_url"] = None  # a link is nice to have; never fail the case for it
    return output, error, meta


def n8n_execution_url(uc: str, call_id: str) -> str | None:
    """Find our execution: the newest ones of the workflow, matched by our `x-kit-call` header.
    Falls back to the workflow's Executions list. Needs N8N_API_KEY."""
    s = settings()
    if not s.n8n_api_key:
        return None
    api = _n8n_api()
    w = _n8n_workflow(api, uc)
    if not w:
        return None
    wid = w["id"]
    for _attempt in range(3):  # n8n saves the execution just after it answers
        data = api.get("/executions", params={"workflowId": wid, "limit": 10, "includeData": "true"}).json()
        for e in data.get("data", []):
            if call_id in json.dumps((e.get("data") or {}).get("resultData", {}).get("runData", {}).get("Case in", [])):
                return f"{s.n8n_url}/workflow/{wid}/executions/{e['id']}"
        time.sleep(1)
    return f"{s.n8n_url}/workflow/{wid}/executions"


def _n8n_api() -> httpx.Client:
    s = settings()
    return httpx.Client(base_url=f"{s.n8n_url}/api/v1", headers={"X-N8N-API-KEY": s.n8n_api_key}, timeout=10)


def _n8n_workflow(api: httpx.Client, uc: str) -> dict | None:
    """The active workflow with the webhook path `uc` (the last one, if there are more)."""
    hit = None
    for w in api.get("/workflows", params={"active": "true", "limit": 100}).json().get("data", []):
        if any(
            n.get("type") == "n8n-nodes-base.webhook" and n.get("parameters", {}).get("path") == uc
            for n in w.get("nodes", [])
        ):
            hit = w
    return hit


def n8n_unpublished(uc: str) -> str | None:
    """A warning if the n8n workflow of `uc` has edits that are not published, else None.
    n8n 2.x saves editor edits as a draft; the webhook (so the eval) runs the published version."""
    if not settings().n8n_api_key:
        return None
    try:
        w = _n8n_workflow(_n8n_api(), uc)
    except (httpx.HTTPError, ValueError):
        return None
    if w and w.get("activeVersionId") and w.get("versionId") != w["activeVersionId"]:
        return (
            f"n8n: '{w['name']}' has changes that are not published. The eval runs the published version: "
            "click Publish in the n8n editor."
        )
    return None


def _post_n8n(uc: str, engine_input: dict, call_id: str):
    s = settings()
    url = f"{s.n8n_url}/webhook/{uc}"
    meta = {"url": url}
    try:
        # n8n sends nothing until the workflow ends, so the read timeout is the total time.
        # call_with_deadline adds a hard wall-clock limit on top.
        r = httpx.post(
            url, json=engine_input, headers={"x-kit-call": call_id}, timeout=httpx.Timeout(TIMEOUTS["n8n"], connect=10)
        )
    except httpx.ConnectError:
        return None, f"cannot reach n8n at {s.n8n_url}: is it running? run `make up` (or check N8N_URL in .env)", meta
    except httpx.TimeoutException:
        return None, f"n8n did not answer in {TIMEOUTS['n8n']} s: look at the execution in n8n (Executions)", meta
    meta["status"] = r.status_code
    if r.status_code == 404:
        return (
            None,
            (
                f"n8n answered 404 for /webhook/{uc}: is the workflow active and is its webhook path "
                f"'{uc}'? run `make setup`"
            ),
            meta,
        )
    if r.status_code >= 400:
        return None, f"n8n answered {r.status_code}: {r.text[:300]} (look at Executions in n8n)", meta
    try:
        value = r.json()
    except ValueError:
        return (
            None,
            f"n8n answered text, not JSON: {r.text[:200]!r}. End the workflow with Respond to Webhook (JSON)",
            meta,
        )
    output, error = _as_result(value)
    return output, error, meta


def read_stream(out: str, limit: int = 4000):
    """Read `claude -p --output-format stream-json` lines. Return (the final result event or None,
    tool calls [{"name", "input", "output"}]). Tool outputs are cut to `limit` characters."""
    result, calls, by_id = None, [], {}
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            result = ev
        content = (ev.get("message") or {}).get("content") if ev.get("type") in ("assistant", "user") else None
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_use" and c.get("name") not in ("ToolSearch", "StructuredOutput"):
                by_id[c.get("id")] = {
                    "name": c.get("name", "").removeprefix("mcp__kit__"),
                    "input": c.get("input"),
                    "output": None,
                }
                calls.append(by_id[c.get("id")])
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in by_id:
                parts = c.get("content")
                text = (
                    parts
                    if isinstance(parts, str)
                    else "\n".join(p.get("text", "") for p in parts or [] if isinstance(p, dict))
                )
                by_id[c["tool_use_id"]]["output"] = text[:limit]
    return result, calls


def call_claude(uc: str, engine_input: dict, extra_env: dict):
    folder = config.USECASES_DIR / uc / "claude"
    if not shutil.which("claude"):
        return None, "`claude` not found on PATH: install Claude Code, then run `claude` once to log in", {}
    if not (folder / ".mcp.json").exists():
        return None, f"usecases/{uc}/claude/.mcp.json not found: the Claude agent folder needs it", {}
    prompt = CLAUDE_PROMPT + json.dumps(engine_input, ensure_ascii=False)
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        ".mcp.json",
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "mcp__kit",
        "--setting-sources",
        "project",
        "--no-session-persistence",
    ]
    # Claude Code checks the answer against the schema (as n8n and python do) and puts it in `structured_output`.
    # Without it, Claude writes the JSON as text, and now and then the text is broken (a stray `<br>`).
    schema = {k: v for k, v in (engine_input.get("output_schema") or {}).items() if k != "$schema"}
    if schema:  # Claude Code does not accept the "$schema" line
        cmd += ["--json-schema", json.dumps(schema)]
    code, out, err = _run(cmd, folder, "", engine_env(extra_env), TIMEOUTS["claude"])
    if code is None:
        return None, f"Claude did not finish in {TIMEOUTS['claude']} s: make the task smaller or check the tools", {}
    data, tool_calls = read_stream(out)
    if data is None:
        return (
            None,
            f"claude exit {code}, no JSON answer: {_tail(err or out)} (is Claude logged in? run `claude`)",
            {"stderr": _tail(err, 20)},
        )
    meta = {
        "cost_usd": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
        "duration_ms": data.get("duration_ms"),
        "models": list(data.get("modelUsage", {})),
        "tool_calls": tool_calls,
        "stderr": _tail(err, 20),
    }
    text = data.get("result") or ""
    if data.get("is_error") or code != 0:
        return None, f"Claude failed ({data.get('subtype', f'exit {code}')}): {text[:300] or _tail(err)}", meta
    value = data.get("structured_output")
    if value is None:  # no schema, or an old Claude Code: read the JSON from the text
        try:
            value = parse_json_text(text)
        except ValueError as e:
            return (
                None,
                f"Claude's answer has no valid JSON ({json_problem(e)}). It starts: {text[:200]!r}. "
                "Tell it in CLAUDE.md to answer only JSON",
                meta,
            )
    output, error = _as_result(value)
    return output, error, meta


def read_tool_log(err: str, limit: int = 4000) -> list[dict]:
    """Tool calls from the stderr lines of agent.py: `tool <name> <args JSON> -> <result as JSON string>`.
    Lines in another form are skipped."""
    calls = []
    for line in err.splitlines():
        head, sep, out = line.partition('} -> "')
        if not line.startswith("tool ") or not sep or " " not in head[5:]:
            continue
        name, args = head[5:].split(" ", 1)
        try:
            calls.append({"name": name, "input": json.loads(args + "}"), "output": json.loads('"' + out)[:limit]})
        except ValueError:
            continue
    return calls


def call_python(uc: str, engine_input: dict, extra_env: dict):
    folder = config.USECASES_DIR / uc / "python"
    if not (folder / "agent.py").exists():
        return None, f"usecases/{uc}/python/agent.py not found", {}
    cmd = ["uv", "run", "--project", str(config.ROOT), "python", "agent.py"]
    code, out, err = _run(
        cmd, folder, json.dumps(engine_input, ensure_ascii=False), engine_env(extra_env), TIMEOUTS["python"]
    )
    # The last log lines of agent.py (long lines cut), and its tool calls for the results page and the judge.
    meta = {"stderr": "\n".join(line[:300] for line in _tail(err, 40).splitlines()), "tool_calls": read_tool_log(err)}
    if code is None:
        return None, f"agent.py did not finish in {TIMEOUTS['python']} s", meta
    if code != 0:
        return None, f"agent.py exit {code}: {_tail(err, 3) or 'no error text'}", meta
    try:
        value = json.loads(out)
    except ValueError:
        return None, f"agent.py stdout is not JSON: {out[:200]!r}. Print only the result; write logs to stderr", meta
    output, error = _as_result(value)
    return output, error, meta
