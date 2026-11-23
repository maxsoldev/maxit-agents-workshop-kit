"""Tokens, cost and model prices (kit/runner/usage.py, the engines' readers, setup's Langfuse prices). No network."""

from __future__ import annotations

import json

import httpx
import pytest

from kit import config, setup
from kit.runner import usage
from kit.runner.engines import claude, python


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    monkeypatch.setenv("KIT_PRICES", "fast=1/2,bad,gpt-6-luna=0.2/1")


def test_prices_defaults_and_overrides():
    p = config.settings().prices
    assert p["fast"] == (1.0, 2.0) and p["gpt-6.1-terra"] == (1.0, 5.0)  # a default stays
    assert p["gpt-6-luna"] == (0.2, 1.0) and "bad" not in p  # KIT_PRICES wins; a bad part is skipped
    assert config.parse_prices("") == {}


def test_price_of_snapshots_and_cost():
    assert usage.price("openai/fast-2026-10-01") == (1.0, 2.0) and usage.price("nope") is None
    assert usage.call("fast", 1000, 500)["cost_usd"] == pytest.approx(0.002)
    assert usage.call("nope", 1000, 500)["cost_usd"] is None
    assert usage.call("nope", 1, 1, 0.5)["cost_usd"] == 0.5  # a cost from the engine (Claude) wins


def test_totals_unknown_is_none():
    assert usage.total(None) == {"tokens_in": None, "tokens_out": None, "cost_usd": None}
    calls = [usage.call("fast", 1000, 0), usage.call("nope", 10, 10)]
    assert usage.total(calls) == {"tokens_in": 1010, "tokens_out": 10, "cost_usd": None}  # a part is not the cost
    cases = [
        {"seconds": 2.0, "tokens_in": 100, "tokens_out": 10, "cost_usd": 0.01, "judge": {"cost_usd": 0.001}},
        {"seconds": 4.0, "tokens_in": None, "tokens_out": None, "cost_usd": None, "judge": None},
    ]
    t = usage.run_totals(cases)
    assert (t["seconds"], t["avg_seconds"], t["cost_usd"], t["avg_cost_usd"]) == (6.0, 3.0, 0.01, 0.01)
    assert (t["cost_cases"], t["judge_cost_usd"], t["avg_tokens_in"]) == (1, 0.001, 100)


def test_claude_model_calls_from_the_result_event():
    ev = {"modelUsage": {"claude-x": {"inputTokens": 5, "cacheReadInputTokens": 90, "cacheCreationInputTokens": 5,
                                      "outputTokens": 7, "costUSD": 0.03}}}  # fmt: skip
    assert claude.model_calls(ev) == [{"model": "claude-x", "tokens_in": 100, "tokens_out": 7, "cost_usd": 0.03}]
    old = {"total_cost_usd": 0.1, "usage": {"input_tokens": 3, "output_tokens": 4}}
    assert claude.model_calls(old) == [{"model": "claude", "tokens_in": 3, "tokens_out": 4, "cost_usd": 0.1}]
    assert claude.model_calls({}) == []


def test_python_model_lines():
    err = 'x\nmodel fast {"input_tokens": 1000, "output_tokens": 500}\nmodel broken {\nmodel nope {"input_tokens": 1}'
    assert python.read_model_log(err) == [
        {"model": "fast", "tokens_in": 1000, "tokens_out": 500, "cost_usd": pytest.approx(0.002)},
        {"model": "nope", "tokens_in": 1, "tokens_out": 0, "cost_usd": None},
    ]


def test_setup_copies_prices_to_langfuse(monkeypatch, capsys):
    """New and changed prices are written; a model with the same prices is left alone; Langfuse's own stay."""
    stored = [
        {"id": "m1", "isLangfuseManaged": False} | setup.price_model("fast", (1.0, 2.0)),
        {"id": "m2", "isLangfuseManaged": False} | setup.price_model("gpt-6-luna", (0.1, 0.5)),
        {"id": "m3", "isLangfuseManaged": True, "modelName": "gpt-6.1-terra"},
    ]
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        if req.method == "GET":
            return httpx.Response(200, json={"data": stored, "meta": {"totalPages": 1}})
        if req.method == "POST":
            calls.append(json.loads(req.content)["modelName"])
        return httpx.Response(200, json={})

    real = httpx.Client
    monkeypatch.setattr(setup.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    setup.register_prices("http://lf", ("pk", "sk"), config.settings().prices)
    assert calls == [
        "GET /api/public/models",
        "DELETE /api/public/models/m2",  # luna has other prices now: made again
        "POST /api/public/models",
        "gpt-6-luna",
        "POST /api/public/models",  # terra: only Langfuse's own model so far
        "gpt-6.1-terra",
    ]
    assert "model prices in Langfuse: gpt-6-luna, gpt-6.1-terra" in capsys.readouterr().out
    m = setup.price_model("gpt-6.1-terra", (1.0, 5.0))
    assert (m["inputPrice"], m["outputPrice"], m["unit"]) == (1e-6, 5e-6, "TOKENS")
    assert m["matchPattern"] == r"(?i)^(openai/)?(gpt\-6\.1\-terra)(-\d{4}-\d{2}-\d{2})?$"
