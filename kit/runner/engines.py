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

import httpx

from kit import config
from kit.config import settings

TIMEOUTS = {"n8n": 120, "claude": 300, "python": 300}
CLAUDE_PROMPT = "Process this case. Answer with only the result JSON.\n\n"


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
    env.update(extra or {})
    return env


def parse_json_text(text: str):
    """Find the result JSON in a text: the last fenced code block, else the whole text."""
    blocks = re.findall(r"```(?:json)?[ \t]*\n(.*?)```", text, re.S)
    candidate = blocks[-1] if blocks else text
    return json.loads(candidate.strip())


def _as_result(value):
    """n8n can answer `[{...}]`: take the one object. Return (dict, None) or (None, error)."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        return None, f"the result must be one JSON object, got {type(value).__name__}"
    return value, None


def _run(cmd: list[str], cwd, stdin: str, env: dict, timeout: int):
    """Run a command; on timeout kill it and its children. Return (code, stdout, stderr), code None = timeout."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        return None, out, err


def _tail(text: str, lines: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def call_n8n(uc: str, engine_input: dict):
    s = settings()
    url = f"{s.n8n_url}/webhook/{uc}"
    meta = {"url": url}
    try:
        r = httpx.post(url, json=engine_input, timeout=TIMEOUTS["n8n"])
    except httpx.ConnectError:
        return None, f"cannot reach n8n at {s.n8n_url}: is it running? run `make up` (or check N8N_URL in .env)", meta
    except httpx.TimeoutException:
        return None, f"n8n did not answer in {TIMEOUTS['n8n']} s: look at the execution in n8n (Executions)", meta
    meta["status"] = r.status_code
    if r.status_code == 404:
        return None, (f"n8n answered 404 for /webhook/{uc}: is the workflow active and is its webhook path "
                      f"'{uc}'? run `make setup`"), meta
    if r.status_code >= 400:
        return None, f"n8n answered {r.status_code}: {r.text[:300]} (look at Executions in n8n)", meta
    try:
        value = r.json()
    except ValueError:
        return None, f"n8n answered text, not JSON: {r.text[:200]!r}. End the workflow with Respond to Webhook (JSON)", meta
    output, error = _as_result(value)
    return output, error, meta


def call_claude(uc: str, engine_input: dict, extra_env: dict):
    folder = config.USECASES_DIR / uc / "claude"
    if not shutil.which("claude"):
        return None, "`claude` not found on PATH: install Claude Code, then run `claude` once to log in", {}
    if not (folder / ".mcp.json").exists():
        return None, f"usecases/{uc}/claude/.mcp.json not found: the Claude agent folder needs it", {}
    prompt = CLAUDE_PROMPT + json.dumps(engine_input, ensure_ascii=False)
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--mcp-config", ".mcp.json", "--strict-mcp-config",
           "--permission-mode", "dontAsk", "--allowedTools", "mcp__kit",
           "--setting-sources", "project", "--no-session-persistence"]
    code, out, err = _run(cmd, folder, "", engine_env(extra_env), TIMEOUTS["claude"])
    if code is None:
        return None, f"Claude did not finish in {TIMEOUTS['claude']} s: make the task smaller or check the tools", {}
    try:
        data = json.loads(out)
    except ValueError:
        return None, f"claude exit {code}, no JSON answer: {_tail(err or out)} (is Claude logged in? run `claude`)", {}
    meta = {"cost_usd": data.get("total_cost_usd"), "turns": data.get("num_turns"),
            "duration_ms": data.get("duration_ms"), "models": list(data.get("modelUsage", {}))}
    text = data.get("result") or ""
    if data.get("is_error") or code != 0:
        return None, f"Claude failed ({data.get('subtype', f'exit {code}')}): {text[:300] or _tail(err)}", meta
    try:
        value = parse_json_text(text)
    except ValueError:
        return None, f"Claude's answer has no valid JSON: {text[:200]!r}. Tell it in CLAUDE.md to answer only JSON", meta
    output, error = _as_result(value)
    return output, error, meta


def call_python(uc: str, engine_input: dict, extra_env: dict):
    folder = config.USECASES_DIR / uc / "python"
    if not (folder / "agent.py").exists():
        return None, f"usecases/{uc}/python/agent.py not found", {}
    cmd = ["uv", "run", "--project", str(config.ROOT), "python", "agent.py"]
    code, out, err = _run(cmd, folder, json.dumps(engine_input, ensure_ascii=False), engine_env(extra_env),
                          TIMEOUTS["python"])
    meta = {"stderr": _tail(err, 20)}
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
