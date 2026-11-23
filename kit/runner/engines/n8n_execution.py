"""Read an n8n execution (the JSON of `GET /api/v1/executions?includeData=true`): where it failed, the tool calls,
and one entry per node run for the trace (contracts §5, §6). Pure functions: no HTTP, no settings."""

from __future__ import annotations

import json


def run_data(execution: dict | None) -> dict:
    """{node name: [node runs]}."""
    return ((execution or {}).get("data") or {}).get("resultData", {}).get("runData", {})


def has_call(execution: dict, call_id: str) -> bool:
    """True if the `Case in` webhook of this execution got our `x-kit-call` header."""
    return call_id in json.dumps(run_data(execution).get("Case in", []))


def failure(execution: dict | None) -> str | None:
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


def tool_call(node: str, r: dict, limit: int) -> dict | None:
    """The tool call of one node run, or None if the run is not a tool call:
    - a node whose output is the answer of the plain HTTP tool call (`{"tool", "input", "output"}`,
      kit/tools.py `POST .../mcp/call/<tool>`: the workflows);
    - a run of a tool node (output `ai_tool`, under an AI Agent node). The MCP node names a tool `<node>__<tool>`."""
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


def tool_calls(execution: dict | None, limit: int = 4000) -> list[dict]:
    """Tool calls [{"name", "input", "output"}] from an n8n execution, in the order they ran."""
    runs = []
    for node, node_runs in run_data(execution).items():
        for r in node_runs:
            call = tool_call(node, r, limit)
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


def _generation(e: dict, r: dict) -> None:
    """Fill in a model sub-node run: the prompt, the text, the model name and the token usage."""
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


def _node(node: str, r: dict, node_type: str, data_of: dict, limit: int) -> dict:
    """One entry of `nodes` (parent still as (node, run index), or None)."""
    data, src = r.get("data") or {}, (r.get("source") or [None])[0] or {}
    sub = next((k for k in [*data, *(r.get("inputOverride") or {})] if k != "main"), None)
    e = {"name": node, "node_type": node_type, "kind": "span", "model": None, "usage": None, "branch": None}
    e["start"] = r.get("startTime") or 0
    e["end"] = e["start"] + (r.get("executionTime") or 0)
    e["error"] = (r.get("error") or {}).get("message") or ("error" if r.get("executionStatus") == "error" else None)
    e["parent"] = (src.get("previousNode"), src.get("previousNodeRun", 0)) if sub else None
    if sub:  # a sub-node: its input is in inputOverride
        e["input"] = _items((r.get("inputOverride") or {}).get(sub) or [])
        e["output"] = _items(data.get(sub) or [])
    else:  # n8n does not store the input of a main node: it is the output of the node before
        prev = data_of.get(src.get("previousNode"), [])
        prev_run = prev[src.get("previousNodeRun", 0)] if src.get("previousNodeRun", 0) < len(prev) else {}
        e["input"] = _items((prev_run.get("data") or {}).get("main") or [], src.get("previousNodeOutput", 0))
        outs = data.get("main") or []
        e["branch"] = next((k for k, rows in enumerate(outs) if rows), None) if len(outs) > 1 else None
        e["output"] = _items(outs, e["branch"] or 0)
    call = tool_call(node, r, limit)
    if call:
        e["kind"], e["input"], e["output"] = "tool", call["input"], call["output"]
        if sub:
            e["name"] = f"{node}: {call['name']}"
    elif sub == "ai_languageModel":
        _generation(e, r)
    elif node_type.endswith(".agent"):
        e["kind"] = "agent"
    elif ".chain" in node_type:
        e["kind"] = "chain"
    e["input"], e["output"] = _cut(e["input"], limit), _cut(e["output"], limit)
    return e


def nodes(execution: dict | None, limit: int = 4000) -> list[dict]:
    """One entry per node run of an n8n execution, in the order they ran, for the Langfuse trace (contracts §6):
    `{name, node_type, kind, start, end (ms), input, output, error, model, usage, branch, parent}`.
    kind: span, tool, generation (model sub-node), chain (LLM chain) or agent. `parent` = the index of the
    AI node that ran this sub-node (model, tool, parser), else None."""
    data_of = run_data(execution)
    types = {n.get("name"): n.get("type", "") for n in ((execution or {}).get("workflowData") or {}).get("nodes", [])}
    runs = sorted(
        ((node, i, r) for node, node_runs in data_of.items() for i, r in enumerate(node_runs)),
        key=lambda x: (x[2].get("executionIndex", 0), x[2].get("startTime", 0)),
    )
    found, where = [], {}
    for node, i, r in runs:
        where[(node, i)] = len(found)
        found.append(_node(node, r, types.get(node, ""), data_of, limit))
    for e in found:
        e["parent"] = where.get(e["parent"]) if e["parent"] else None
    for e in found:  # an AI node spans the time of its sub-nodes (the agent node starts after its first model call)
        p = e["parent"]
        while p is not None:
            found[p]["start"], found[p]["end"] = min(found[p]["start"], e["start"]), max(found[p]["end"], e["end"])
            p = found[p]["parent"]
    return found
