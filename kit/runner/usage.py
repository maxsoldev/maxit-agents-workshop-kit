"""Tokens and cost of model calls (contracts §5). A model call is
`{"model", "tokens_in", "tokens_out", "cost_usd"}`; cost_usd is None when the kit has no price for the model.
Prices: `settings().prices` (KIT_PRICES, USD per 1M tokens). Claude Code reports its own cost."""

from __future__ import annotations

import re

from kit.config import settings


def price(model: str | None) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens. A dated snapshot (`gpt-6-luna-2026-10-01`) and a provider
    prefix (`openai/gpt-6-luna`) have the price of the model name."""
    prices = settings().prices
    name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", (model or "").split("/")[-1])
    return prices.get(model or "") or prices.get(name)


def call(model: str | None, tokens_in: int, tokens_out: int, cost_usd: float | None = None) -> dict:
    """One model call. Without `cost_usd`, the cost comes from the price table (None if no price)."""
    if cost_usd is None and (p := price(model)):
        cost_usd = (tokens_in * p[0] + tokens_out * p[1]) / 1e6
    return {"model": model, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd}


def total(calls: list[dict] | None) -> dict:
    """Sum of the calls: `{"tokens_in", "tokens_out", "cost_usd"}`. No calls: all None (unknown).
    cost_usd is None if one call has no cost: a part of the cost is not the cost."""
    if not calls:
        return {"tokens_in": None, "tokens_out": None, "cost_usd": None}
    costs = [c.get("cost_usd") for c in calls]
    return {
        "tokens_in": sum(c.get("tokens_in") or 0 for c in calls),
        "tokens_out": sum(c.get("tokens_out") or 0 for c in calls),
        "cost_usd": None if None in costs else round(sum(costs), 6),
    }


def run_totals(cases: list[dict]) -> dict:
    """Run totals and averages per case, over the cases where the value is known.
    `{"seconds", "tokens_in", "tokens_out", "cost_usd", "cost_cases", "judge_cost_usd",
    "avg_seconds", "avg_tokens_in", "avg_tokens_out", "avg_cost_usd"}`; a value no case has is None."""
    out: dict = {}
    for key in ("seconds", "tokens_in", "tokens_out", "cost_usd"):
        known = [c[key] for c in cases if c.get(key) is not None]
        out[key] = round(sum(known), 6 if key == "cost_usd" else 1) if known else None
        out[f"avg_{key}"] = round(sum(known) / len(known), 6 if key == "cost_usd" else 1) if known else None
    out["cost_cases"] = sum(1 for c in cases if c.get("cost_usd") is not None)
    judge = [(c.get("judge") or {}).get("cost_usd") for c in cases if c.get("judge")]
    out["judge_cost_usd"] = (
        round(sum(j for j in judge if j is not None), 6) if any(j is not None for j in judge) else None
    )
    return out
