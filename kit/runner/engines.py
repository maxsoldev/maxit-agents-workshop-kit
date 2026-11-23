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

TIMEOUTS = {"n8n": 120, "n8n-agent": 120, "claude": 300, "python": 300}
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
    if engine in ("n8n", "n8n-agent"):
        return call_n8n(n8n_hook(uc, engine), engine_input)
    if engine == "claude":
        return call_claude(uc, engine_input, extra_env or {})
    if engine == "python":
        return call_python(uc, engine_input, extra_env or {})
    return None, f"unknown engine {engine!r}; use one of {', '.join(config.ALL_ENGINES)}", {}


def n8n_hook(uc: str, engine: str = "n8n") -> str:
    """The webhook path: `<uc>` for the workflow (n8n), `<uc>-agent` for the agent example (n8n-agent)."""
    return f"{uc}-agent" if engine == "n8n-agent" else uc


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


def call_n8n(hook: str, engine_input: dict):
    """POST the case to the webhook path `hook`. meta["execution_url"] links to the n8n execution (if found);
    meta["tool_calls"] and meta["n8n_nodes"] (for the trace, not the run file) are read from that execution's data."""
    call_id = uuid.uuid4().hex
    output, error, meta = _post_n8n(hook, engine_input, call_id)
    if meta.get("status"):  # n8n answered, so there is an execution
        try:
            meta["execution_url"], execution = n8n_execution(hook, call_id)
            meta["tool_calls"] = n8n_tool_calls(execution)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            meta["execution_url"] = None  # a link is nice to have; never fail the case for it
            execution = None
        if error and (failure := n8n_failure(execution)):  # n8n only says "Error in workflow": say where and why
            error = f"{failure} (open the n8n execution: {meta['execution_url']})"
        try:
            meta["n8n_nodes"] = n8n_nodes(execution)
        except Exception:
            pass  # the node spans are nice to have; never fail the case for them
    return output, error, meta


def n8n_execution(hook: str, call_id: str) -> tuple[str | None, dict | None]:
    """Find our execution: the newest ones of the workflow, matched by our `x-kit-call` header.
    Return (its URL, the execution with data). Falls back to the workflow's Executions list and None.
    Needs N8N_API_KEY."""
    s = settings()
    if not s.n8n_api_key:
        return None, None
    api = _n8n_api()
    w = _n8n_workflow(api, hook)
    if not w:
        return None, None
    wid = w["id"]
    for _attempt in range(3):  # n8n saves the execution just after it answers
        data = api.get("/executions", params={"workflowId": wid, "limit": 10, "includeData": "true"}).json()
        for e in data.get("data", []):
            if call_id in json.dumps((e.get("data") or {}).get("resultData", {}).get("runData", {}).get("Case in", [])):
                return f"{s.n8n_url}/workflow/{wid}/executions/{e['id']}", e
        time.sleep(1)
    return f"{s.n8n_url}/workflow/{wid}/executions", None


def n8n_failure(execution: dict | None) -> str | None:
    """`n8n failed at <node>: <message>` for a failed execution, else None. A sub-node error (e.g. a parser)
    names its AI node too: `Triage > Triage fields`."""
    result = ((execution or {}).get("data") or {}).get("resultData") or {}
    error = result.get("error") or {}
    if not error:
        return None
    node, last = (error.get("node") or {}).get("name"), result.get("lastNodeExecuted")
    where = f"{last} > {node}" if node and last and node != last else (node or last or "a node")
    why = (error.get("context") or {}).get("outputParserFailReason")
    return f"n8n failed at {where}: {error.get('message') or 'error'}" + (f" ({why})" if why else "")


def _first_json(part, kind: str = "ai_tool", index: int = 0) -> dict | None:
    """{"ai_tool": [[{"json": {...}}]]} -> {...} (the first item of output `index`)."""
    lists = (part or {}).get(kind) or []
    items = (lists[index] if index < len(lists) else None) or [{}]
    return items[0].get("json") if isinstance(items[0], dict) else None


def _n8n_run_data(execution: dict | None) -> dict:
    return ((execution or {}).get("data") or {}).get("resultData", {}).get("runData", {})


def _n8n_tool_call(node: str, r: dict, limit: int) -> dict | None:
    """The tool call of one node run, or None if the run is not a tool call:
    - a node whose output is the answer of the plain HTTP tool call (`{"tool", "input", "output"}`,
      kit/tools.py `POST .../mcp/call/<tool>`: the workflows);
    - a run of a tool node (output `ai_tool`: the agent example). The MCP node names a tool `<node>__<tool>`."""
    http = _first_json(r.get("data"), "main") or {}
    if {"tool", "input", "output"} <= set(http):
        out = http["output"] if isinstance(http["output"], str) else json.dumps(http["output"], ensure_ascii=False)
        return {"name": str(http["tool"]), "input": http["input"], "output": out[:limit]}
    if "ai_tool" not in (r.get("data") or {}) and "ai_tool" not in (r.get("inputOverride") or {}):
        return None
    args = dict(_first_json(r.get("inputOverride")) or {})
    name = str(args.pop("tool", "") or node).split("__")[-1]
    out = (_first_json(r.get("data")) or {}).get("response")
    if isinstance(out, list):  # MCP content: [{"type": "text", "text": ...}]
        out = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in out)
    elif not isinstance(out, str):
        out = json.dumps(out, ensure_ascii=False) if out is not None else (r.get("error") or {}).get("message", "")
    return {"name": name, "input": args, "output": str(out)[:limit]}


def n8n_tool_calls(execution: dict | None, limit: int = 4000) -> list[dict]:
    """Tool calls [{"name", "input", "output"}] from an n8n execution, in the order they ran."""
    runs = []
    for node, node_runs in _n8n_run_data(execution).items():
        for r in node_runs:
            call = _n8n_tool_call(node, r, limit)
            if call:
                runs.append((r.get("executionIndex", 0), call))
    return [call for _, call in sorted(runs, key=lambda x: x[0])]


def _cut(value, limit: int):
    """The value itself if its JSON is short, else its JSON cut to `limit` characters."""
    if isinstance(value, str):
        return value[:limit]
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else text[:limit]


def _items(lists: list, index: int = 0):
    """The `json` of the items of output `index`: one item -> the object, more -> a list, none -> None."""
    rows = (lists[index] if index < len(lists) else None) or []
    found = [row.get("json") for row in rows if isinstance(row, dict)]
    return found[0] if len(found) == 1 else (found or None)


def n8n_nodes(execution: dict | None, limit: int = 4000) -> list[dict]:
    """One entry per node run of an n8n execution, in the order they ran, for the Langfuse trace (contracts §6):
    `{name, node_type, kind, start, end (ms), input, output, error, model, usage, branch, parent}`.
    kind: span, tool, generation (model sub-node), chain (LLM chain) or agent. `parent` = the index of the
    AI node that ran this sub-node (model, tool, parser), else None. The input of a main node is the output
    of the node before it (n8n does not store it)."""
    run_data = _n8n_run_data(execution)
    types = {n.get("name"): n.get("type", "") for n in ((execution or {}).get("workflowData") or {}).get("nodes", [])}
    runs = sorted(
        ((node, i, r) for node, node_runs in run_data.items() for i, r in enumerate(node_runs)),
        key=lambda x: (x[2].get("executionIndex", 0), x[2].get("startTime", 0)),
    )
    nodes, where = [], {}
    for node, i, r in runs:
        data, src = r.get("data") or {}, (r.get("source") or [None])[0] or {}
        sub = next((k for k in [*data, *(r.get("inputOverride") or {})] if k != "main"), None)
        node_type = types.get(node, "")
        e = {"name": node, "node_type": node_type, "kind": "span", "model": None, "usage": None, "branch": None}
        e["start"] = r.get("startTime") or 0
        e["end"] = e["start"] + (r.get("executionTime") or 0)
        e["error"] = (r.get("error") or {}).get("message") or ("error" if r.get("executionStatus") == "error" else None)
        e["parent"] = (src.get("previousNode"), src.get("previousNodeRun", 0)) if sub else None
        if sub:  # a sub-node: its input is in inputOverride
            e["input"] = _items((r.get("inputOverride") or {}).get(sub) or [])
            e["output"] = _items(data.get(sub) or [])
        else:
            prev = run_data.get(src.get("previousNode"), [])
            prev_run = prev[src.get("previousNodeRun", 0)] if src.get("previousNodeRun", 0) < len(prev) else {}
            e["input"] = _items((prev_run.get("data") or {}).get("main") or [], src.get("previousNodeOutput", 0))
            outs = data.get("main") or []
            e["branch"] = next((k for k, rows in enumerate(outs) if rows), None) if len(outs) > 1 else None
            e["output"] = _items(outs, e["branch"] or 0)
        call = _n8n_tool_call(node, r, limit)
        if call:
            e["kind"], e["input"], e["output"] = "tool", call["input"], call["output"]
            if sub:
                e["name"] = f"{node}: {call['name']}"
        elif sub == "ai_languageModel":
            out, tracing = e["output"] or {}, (r.get("metadata") or {}).get("tracing") or {}
            gens = [g for batch in (out.get("response") or {}).get("generations") or [] for g in batch]
            e["kind"], e["input"] = "generation", (e["input"] or {}).get("messages", e["input"])
            e["output"] = "\n".join(str(g.get("text", "")) for g in gens) or out
            e["model"] = tracing.get("gen_ai.response.model") or tracing.get("gen_ai.request.model")
            # With the Responses API (the workflows' model nodes) n8n 2.x stores only its own estimate.
            tokens = out.get("tokenUsage") or out.get("tokenUsageEstimate") or {}
            usage = {"input": tokens.get("promptTokens"), "output": tokens.get("completionTokens")}
            usage["total"] = tokens.get("totalTokens")
            e["usage"] = {k: v for k, v in usage.items() if isinstance(v, int)} or None
        elif node_type.endswith(".agent"):
            e["kind"] = "agent"
        elif ".chain" in node_type:
            e["kind"] = "chain"
        e["input"], e["output"] = _cut(e["input"], limit), _cut(e["output"], limit)
        where[(node, i)] = len(nodes)
        nodes.append(e)
    for e in nodes:
        e["parent"] = where.get(e["parent"]) if e["parent"] else None
    for e in nodes:  # an AI node spans the time of its sub-nodes (the agent node starts after its first model call)
        p = e["parent"]
        while p is not None:
            nodes[p]["start"], nodes[p]["end"] = min(nodes[p]["start"], e["start"]), max(nodes[p]["end"], e["end"])
            p = nodes[p]["parent"]
    return nodes


def _n8n_api() -> httpx.Client:
    s = settings()
    return httpx.Client(base_url=f"{s.n8n_url}/api/v1", headers={"X-N8N-API-KEY": s.n8n_api_key}, timeout=10)


def _n8n_workflow(api: httpx.Client, hook: str) -> dict | None:
    """The active workflow with the webhook path `hook` (the last one, if there are more)."""
    hit = None
    for w in api.get("/workflows", params={"active": "true", "limit": 100}).json().get("data", []):
        if any(
            n.get("type") == "n8n-nodes-base.webhook" and n.get("parameters", {}).get("path") == hook
            for n in w.get("nodes", [])
        ):
            hit = w
    return hit


def n8n_warning(uc: str, engine: str = "n8n") -> str | None:
    """A warning about the n8n workflow of `uc` (engine n8n or n8n-agent), else None: the files and n8n are
    not in sync (kit/setup.py `sync_advice`), or n8n has edits that are not published (the webhook, so the
    eval, runs the published version)."""
    from kit import setup  # setup owns the sync state

    st = setup.n8n_status(uc, engine)
    if not st:
        return None
    rel = st["path"].relative_to(config.ROOT) if st["path"].is_relative_to(config.ROOT) else st["path"]
    found = []
    if st["state"] == "n8n":
        found.append(f"'{st['name']}' has n8n edits that are not in {rel} yet: the eval saves them to the files.")
    elif st["state"] != "same":
        found.append(setup.sync_advice(st["state"], uc, rel, st["name"]))
    if st["draft"]:
        found.append(setup.draft_advice(uc, st["name"]))
    return " ".join(found) or None


def _post_n8n(hook: str, engine_input: dict, call_id: str):
    s = settings()
    url = f"{s.n8n_url}/webhook/{hook}"
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
                f"n8n answered 404 for /webhook/{hook}: is the workflow active and is its webhook path "
                f"'{hook}'? run `make setup`"
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


def builder_excludes(folder) -> dict:
    """Settings that keep the builder's files out of the engine. Claude Code also loads the CLAUDE.md files
    and the .claude/skills of the parent folders (the repo root: for your Claude Code session, not the agent)."""
    mds, skills, d = [], {}, folder.parent
    while True:
        for p in {d, d.resolve()}:
            mds += [str(p / "CLAUDE.md"), str(p / "CLAUDE.local.md")]
        skills.update({s.name: "off" for s in (d / ".claude" / "skills").glob("*/") if s.is_dir()})
        if d == config.ROOT or d.parent == d or not d.is_relative_to(config.ROOT):
            break
        d = d.parent
    for own in (folder / ".claude" / "skills").glob("*/"):  # a skill of the agent with the same name stays on
        skills.pop(own.name, None)
    return {"claudeMdExcludes": sorted(set(mds)), "skillOverrides": skills}


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
        "--settings",
        json.dumps(builder_excludes(folder)),
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
