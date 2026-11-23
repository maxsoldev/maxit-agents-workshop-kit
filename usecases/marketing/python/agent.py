"""Marketing inbox agent, Python engine (OpenAI Responses API + kit tools over MCP).

The runner sends the case on stdin and reads the result JSON from stdout (docs/contracts.md §4).
Logs go to stderr. The prompt is in prompt.md, the owner's rules in rules/*.md. The model and the steps are here.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import Client
from openai import OpenAI

MODEL = os.environ.get("KIT_MODEL", "gpt-6-luna")
TOOLS_URL = os.environ.get("KIT_TOOLS_URL", "http://localhost:5701/mcp")
# The runner sends the result shape as case["output_schema"]; the file is the fallback for runs by hand.
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "output.schema.json"
MAX_STEPS = 10  # model turns before we give up

HERE = Path(__file__).resolve().parent
# The instructions are in prompt.md, the owner's rules in rules/*.md: plain text files, edit them in the kit UI (Rules).
PROMPT_FILE = HERE / "prompt.md"
RULES_DIR = HERE / "rules"


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


def log_usage(answer) -> None:
    """One line per model call: the runner reads the tokens from it (cost per case)."""
    u = getattr(answer, "usage", None)
    if u:
        log(f"model {MODEL} {json.dumps({'input_tokens': u.input_tokens, 'output_tokens': u.output_tokens})}")


def to_openai_tool(tool) -> dict:
    """An MCP tool description in the format of the OpenAI Responses API.
    strict=False: MCP schemas do not follow the strict rules (the Responses API default is strict)."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.input_schema,
        "strict": False,
    }


def result_format(schema: dict) -> dict:
    """The result schema as an OpenAI result format. Only the shape goes to the model: with "title"
    in it, the model may copy "title" into the result. strict=True makes the API keep to the shape;
    it needs every field in "required" and "additionalProperties": false. Else strict=False."""
    shape = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title")}
    return {"type": "json_schema", "name": "result", "schema": shape, "strict": strict_ok(shape)}


def strict_ok(node) -> bool:
    """True if every object in the schema has all its fields required and no other fields allowed."""
    if isinstance(node, list):
        return all(strict_ok(x) for x in node)
    if not isinstance(node, dict):
        return True
    if node.get("type") == "object":
        fields = node.get("properties", {})
        if node.get("additionalProperties") is not False or set(node.get("required", [])) != set(fields):
            return False
    return all(strict_ok(v) for v in node.values())


async def call_tool(client: Client, name: str, args: dict) -> str:
    result = await client.call_tool(name, args)
    return "\n".join(getattr(c, "text", "") for c in result.content)


def instructions() -> str:
    """prompt.md, then all the owner's rules (rules/*.md), read from disk at the start of each case.
    So the model sees every rule, also one it would not look up, and an edit counts from the next case."""
    rules = "\n\n".join(f"=== {f.name} ===\n{f.read_text(encoding='utf-8')}" for f in sorted(RULES_DIR.glob("*.md")))
    log(f"rules: {', '.join(f.name for f in sorted(RULES_DIR.glob('*.md'))) or 'none'}")
    return f"{PROMPT_FILE.read_text(encoding='utf-8')}\nThe owner's rules (python/rules/*.md). Follow them:\n\n{rules}"


async def run(case: dict, client: Client | None) -> dict:
    llm = OpenAI()  # reads OPENAI_API_KEY (and OPENAI_BASE_URL for a gateway)
    schema = case.get("output_schema") or json.loads(SCHEMA_FILE.read_text())
    tools = [to_openai_tool(t) for t in (await client.list_tools()).tools] if client else []
    with_tools = {"tools": tools} if tools else {}  # some APIs refuse an empty tool list
    # The conversation: our messages, then the model's output items and our tool results.
    prompt = instructions()
    items = [
        {
            "role": "user",
            "content": f"Today is {case.get('today')}.\n\n" + json.dumps(case["input"], ensure_ascii=False),
        }
    ]
    # Tool turns first, WITHOUT a result format: with it, many models never call a tool.
    # When the model stops calling tools, one last call WITH the result format gives the result JSON.
    for _ in range(MAX_STEPS):
        answer = llm.responses.create(model=MODEL, instructions=prompt, input=items, **with_tools)
        log_usage(answer)
        items += answer.output  # reasoning, messages and function calls go back as they came
        calls = [item for item in answer.output if item.type == "function_call"]
        if not calls:
            break
        for call in calls:
            args = json.loads(call.arguments or "{}")
            text = await call_tool(client, call.name, args)
            # One line per tool call: the runner reads these lines and gives them to the judge.
            log(
                f"tool {call.name} {json.dumps(args, ensure_ascii=False)} -> "
                f"{json.dumps(text[:2000], ensure_ascii=False)}"
            )
            items.append({"type": "function_call_output", "call_id": call.call_id, "output": text})
    else:
        log(f"still calling tools after {MAX_STEPS} steps: asking for the result now")
    items.append({"role": "user", "content": "Now return only the result JSON, with only the fields of the schema."})
    if tools:
        with_tools["tool_choice"] = "none"  # the last turn gives the result, no more tool calls
    final = llm.responses.create(
        model=MODEL,
        instructions=prompt,
        input=items,
        **with_tools,
        text={"format": result_format(schema)},
    )
    log_usage(final)
    return json.loads(final.output_text)


async def main() -> None:
    case = json.load(sys.stdin)
    async with AsyncExitStack() as stack:
        try:
            client = await stack.enter_async_context(Client(TOOLS_URL))
        except Exception as err:  # tools server is down: work without tools
            log(f"tools not reachable at {TOOLS_URL} ({err!r}); running without tools")
            client = None
        result = await run(case, client)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
