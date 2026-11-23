"""The Python scaffold agents with a fake OpenAI server (no network): the Responses API tool loop."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import types
from pathlib import Path

import httpx
import openai
import pytest

USECASES = Path(__file__).resolve().parent.parent / "usecases"


def load_agent(uc: str):
    spec = importlib.util.spec_from_file_location(f"agent_{uc}", USECASES / uc / "python" / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTools:
    """A fake MCP client with one tool."""

    def __init__(self):
        self.calls = []

    async def list_tools(self):
        tool = types.SimpleNamespace(
            name="rules_get",
            description="Read a rule file.",
            input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        )
        return types.SimpleNamespace(tools=[tool])

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return types.SimpleNamespace(content=[types.SimpleNamespace(text="rule text")])


def fake_openai(requests: list[dict]):
    """An OpenAI client whose /responses answers: 1st a function call, then text, then the result JSON."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        n = len(requests)
        if n == 1:
            output = [
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "rules_get",
                    "arguments": '{"name": "process"}',
                    "status": "completed",
                }
            ]
        else:
            text = "done" if n == 2 else '{"ok": true}'
            output = [
                {
                    "type": "message",
                    "id": f"msg_{n}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": f"resp_{n}",
                "object": "response",
                "created_at": 0,
                "model": body["model"],
                "status": "completed",
                "output": output,
            },
        )

    return openai.OpenAI(
        api_key="test", base_url="http://fake/v1", http_client=httpx.Client(transport=httpx.MockTransport(handle))
    )


@pytest.mark.parametrize("uc", ["marketing", "travel"])
def test_agent_runs_the_tool_loop_on_the_responses_api(uc, monkeypatch):
    agent = load_agent(uc)
    requests: list[dict] = []
    monkeypatch.setattr(agent, "OpenAI", lambda: fake_openai(requests))
    tools = FakeTools()
    case = {"today": "2026-11-24", "input": {"email": "Hello"}, "output_schema": {"type": "object"}}
    result = asyncio.run(agent.run(case, tools))

    assert result == {"ok": True}
    assert tools.calls == [("rules_get", {"name": "process"})]
    assert len(requests) == 3
    first, second, last = requests
    assert first["tools"][0]["name"] == "rules_get" and first["tools"][0]["strict"] is False
    assert "text" not in first  # no result format in the tool turns
    # The tool result goes back with the call id, after the model's function_call item.
    assert {"type": "function_call_output", "call_id": "call_1", "output": "rule text"} in second["input"]
    assert any(i.get("type") == "function_call" and i["call_id"] == "call_1" for i in second["input"])
    assert last["tool_choice"] == "none"
    assert last["text"]["format"]["type"] == "json_schema"


def test_agent_without_tools_sends_no_tool_list(monkeypatch):
    agent = load_agent("marketing")
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        msg = {
            "type": "message",
            "id": "m",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": '{"ok": 1}', "annotations": []}],
        }
        return httpx.Response(
            200,
            json={
                "id": "r",
                "object": "response",
                "created_at": 0,
                "model": "m",
                "status": "completed",
                "output": [msg],
            },
        )

    client = openai.OpenAI(
        api_key="test", base_url="http://fake/v1", http_client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    monkeypatch.setattr(agent, "OpenAI", lambda: client)
    assert asyncio.run(agent.run({"today": "x", "input": {}, "output_schema": {}}, None)) == {"ok": 1}
    assert all("tools" not in r and "tool_choice" not in r for r in requests)
