"""What the engines share: the process env, a subprocess with a hard timeout, and reading the result JSON."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess

from kit.config import settings

# What an engine call returns: (output, error, meta). output = the result object or None; error = a text that
# says what to do, or None; meta = extra facts for the run file (tool_calls, stderr, cost, links).
Answer = tuple[dict | None, str | None, dict]

TIMEOUTS = {"n8n": 120, "claude": 300, "python": 300}  # seconds, wall clock (contracts §4)


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


def run_process(cmd: list[str], cwd, stdin: str, env: dict, timeout: int) -> tuple[int | None, str, str]:
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


def tail(text: str, lines: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def as_result(value) -> tuple[dict | None, str | None]:
    """The result must be one object; `[{...}]` (n8n can answer so) counts as that object."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        return None, f"the result must be one JSON object, got {type(value).__name__}"
    return value, None


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
