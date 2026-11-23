"""Engine `claude`: `claude -p` in `usecases/<uc>/claude/`, stream-json out (contracts §4)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from kit import config
from kit.runner import usage
from kit.runner.engines.common import (
    TIMEOUTS,
    Answer,
    as_result,
    engine_env,
    json_problem,
    parse_json_text,
    run_process,
    tail,
)

PROMPT = "Process this case. Answer with only the result JSON.\n\n"
HIDDEN_TOOLS = ("ToolSearch", "StructuredOutput")  # Claude Code's own tools: not agent tool calls


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
            if c.get("type") == "tool_use" and c.get("name") not in HIDDEN_TOOLS:
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


def model_calls(result: dict) -> list[dict]:
    """One model call per model from the `result` event (`modelUsage`), with Claude Code's own cost.
    tokens_in counts the cache reads and writes too (Claude sends most input from its cache)."""
    calls = []
    for model, u in (result.get("modelUsage") or {}).items():
        tokens_in = sum(u.get(k) or 0 for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
        calls.append(usage.call(model, tokens_in, u.get("outputTokens") or 0, u.get("costUSD")))
    if not calls and result.get("total_cost_usd") is not None:  # an older Claude Code: only the totals
        u = result.get("usage") or {}
        tokens_in = sum(
            u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        calls.append(usage.call("claude", tokens_in, u.get("output_tokens") or 0, result["total_cost_usd"]))
    return calls


def builder_excludes(folder) -> dict:
    """Settings that keep other instructions out of the engine. Claude Code also loads the CLAUDE.md files
    of the parent folders and of ~/.claude, and the .claude/skills of the parent folders (the repo root: for
    your Claude Code session, not the agent). The agent gets only its own folder."""
    mds = [str(Path.home() / ".claude" / "CLAUDE.md")]
    skills = {}
    for d in [folder.parent, *folder.parent.parents]:
        for p in {d, d.resolve()}:
            mds += [str(p / "CLAUDE.md"), str(p / "CLAUDE.local.md")]
        if d.is_relative_to(config.ROOT):  # the user's own skills (~/.claude) do not load with --setting-sources
            skills.update({s.name: "off" for s in (d / ".claude" / "skills").glob("*/") if s.is_dir()})
    for own in (folder / ".claude" / "skills").glob("*/"):  # a skill of the agent with the same name stays on
        skills.pop(own.name, None)
    return {"claudeMdExcludes": sorted(set(mds)), "skillOverrides": skills}


def command(folder: Path, engine_input: dict) -> list[str]:
    cmd = [
        "claude",
        "-p",
        PROMPT + json.dumps(engine_input, ensure_ascii=False),
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
    return cmd


def call(uc: str, engine_input: dict, extra_env: dict) -> Answer:
    folder = config.USECASES_DIR / uc / "claude"
    if not shutil.which("claude"):
        return None, "`claude` not found on PATH: install Claude Code, then run `claude` once to log in", {}
    if not (folder / ".mcp.json").exists():
        return None, f"usecases/{uc}/claude/.mcp.json not found: the Claude agent folder needs it", {}
    timeout = TIMEOUTS["claude"]
    code, out, err = run_process(command(folder, engine_input), folder, "", engine_env(extra_env), timeout)
    if code is None:
        return None, f"Claude did not finish in {timeout} s: make the task smaller or check the tools", {}
    data, tool_calls = read_stream(out)
    if data is None:
        return (
            None,
            f"claude exit {code}, no JSON answer: {tail(err or out)} (is Claude logged in? run `claude`)",
            {"stderr": tail(err, 20)},
        )
    meta = {
        "cost_usd": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
        "duration_ms": data.get("duration_ms"),
        "models": list(data.get("modelUsage", {})),
        "model_calls": model_calls(data),
        "tool_calls": tool_calls,
        "stderr": tail(err, 20),
    }
    text = data.get("result") or ""
    if data.get("is_error") or code != 0:
        return None, f"Claude failed ({data.get('subtype', f'exit {code}')}): {text[:300] or tail(err)}", meta
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
    output, error = as_result(value)
    return output, error, meta
