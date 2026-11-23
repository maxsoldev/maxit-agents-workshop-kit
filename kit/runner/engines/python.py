"""Engine `python`: `usecases/<uc>/python/agent.py`, engine input on stdin, result JSON on stdout (contracts §4)."""

from __future__ import annotations

import json

from kit import config
from kit.runner.engines.common import TIMEOUTS, Answer, as_result, engine_env, run_process, tail


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


def call(uc: str, engine_input: dict, extra_env: dict) -> Answer:
    folder = config.USECASES_DIR / uc / "python"
    if not (folder / "agent.py").exists():
        return None, f"usecases/{uc}/python/agent.py not found", {}
    cmd = ["uv", "run", "--project", str(config.ROOT), "python", "agent.py"]
    timeout = TIMEOUTS["python"]
    code, out, err = run_process(
        cmd, folder, json.dumps(engine_input, ensure_ascii=False), engine_env(extra_env), timeout
    )
    # The last log lines of agent.py (long lines cut), and its tool calls for the results page and the judge.
    meta = {"stderr": "\n".join(line[:300] for line in tail(err, 40).splitlines()), "tool_calls": read_tool_log(err)}
    if code is None:
        return None, f"agent.py did not finish in {timeout} s", meta
    if code != 0:
        return None, f"agent.py exit {code}: {tail(err, 3) or 'no error text'}", meta
    try:
        value = json.loads(out)
    except ValueError:
        return None, f"agent.py stdout is not JSON: {out[:200]!r}. Print only the result; write logs to stderr", meta
    output, error = as_result(value)
    return output, error, meta
