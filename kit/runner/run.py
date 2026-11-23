"""Run an eval: the cases on one engine, each checked and traced, into one run file (contracts §5)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from kit import config
from kit.runner import cases, checks, engines, tracing, usage
from kit.runner.store import new_run_id, save_run


def run_case(uc: str, engine: str, case: dict, schema: dict, run_id: str, run_name: str, experiment=None) -> dict:
    """Run one case and check it. Never raises: a crash is a failed case with `error`.
    `experiment` (from `tracing.experiment_for_run`): link the case trace to the Langfuse dataset run."""
    t0 = time.time()  # wall clock: on macOS, monotonic time stops while the computer sleeps
    agent_s = None
    res = {
        "id": case["id"],
        "title": case.get("title", ""),
        "today": case.get("today"),
        "case_hash": cases.case_hash(case),
        "passed": False,
        "seconds": 0.0,  # the agent's time (the engine call), without the checks
        **usage.total(None),  # tokens_in, tokens_out, cost_usd of the agent (None = unknown)
        "judge": None,  # {"seconds", "tokens_in", "tokens_out", "cost_usd"} if a judge check ran
        "error": None,
        "input": case.get("input", {}),
        "expected": case.get("expected", {}),
        "output": None,
        "checks": [],
        "trace_url": None,
        "n8n_execution_url": None,
        "meta": {},
    }
    try:
        inp = cases.engine_input(uc, case)
        res["input"] = inp["input"]
        inp["output_schema"] = schema  # every engine sees the result shape (contracts §4)
        with tracing.case_trace(uc, engine, run_id, run_name, case, inp, experiment) as trace:
            res["trace_url"] = trace.url
            otel = getattr(engines.ENGINES.get(engine), "otel", False)
            t_agent = time.time()
            output, error, meta = engines.call_with_deadline(uc, engine, inp, trace.otel_env() if otel else {})
            agent_s = round(time.time() - t_agent, 1)
            trace.agent_done()  # the trace's latency is the agent's time, as `seconds`
            res.update(output=output, error=error, meta=meta, **usage.total(meta.get("model_calls")))
            res["n8n_execution_url"] = meta.pop("execution_url", None)
            nodes = meta.pop("n8n_nodes", None) or []
            trace.n8n_nodes(nodes)  # n8n: the model nodes are the generations
            trace.model_calls([] if nodes else meta.get("model_calls") or [], "model")
            trace.tool_calls(meta.get("tool_calls") or [])
            t_checks = time.time()
            res["checks"] = checks.run_checks(case, output, schema, inp["input"], meta.get("tool_calls"))
            judged = [c["model_call"] for c in res["checks"] if c.get("model_call")]
            if judged:
                res["judge"] = {"seconds": round(time.time() - t_checks, 1), **usage.total(judged)}
            trace.model_calls(judged, "judge")
            res["passed"] = error is None and all(c["passed"] is not False for c in res["checks"])  # skipped ok
            trace.finish(output, error, res["checks"], res["passed"])
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        res["passed"] = False
    res["seconds"] = agent_s if agent_s is not None else round(time.time() - t0, 1)
    return res


def summarize(case_results: list[dict]) -> dict:
    """Counts per check type and per check type + field (`equals action`: [8, 15]).
    Skipped checks (a judge that could not run) are counted apart."""
    by_check: dict[str, list[int]] = {}
    by_field: dict[str, list[int]] = {}
    skipped: dict[str, int] = {}
    for r in case_results:
        for c in r["checks"]:
            if c.get("skipped"):
                skipped[c["type"]] = skipped.get(c["type"], 0) + 1
                continue
            name = f"{c['type']} {c['field']}".strip() if c["type"] != "schema" else "schema"
            for counts in (by_check.setdefault(c["type"], [0, 0]), by_field.setdefault(name, [0, 0])):
                counts[0] += c["passed"] is True
                counts[1] += 1
    return {
        "cases": len(case_results),
        "passed": sum(r["passed"] for r in case_results),
        "by_check": by_check,
        "by_field": by_field,
        "skipped": skipped,
        "totals": usage.run_totals(case_results),
    }


def pick_cases(all_cases: list[dict], case_ids, holdout: bool) -> list[dict]:
    """The cases named in `case_ids` (ValueError for an unknown id), else all but the holdout ones."""
    if case_ids:
        unknown = [i for i in case_ids if i not in {c["id"] for c in all_cases}]
        if unknown:
            raise ValueError(f"unknown case id(s): {', '.join(unknown)}")
        return [c for c in all_cases if c["id"] in case_ids]
    if holdout:
        return all_cases
    return [c for c in all_cases if not c.get("holdout")]  # kept aside for the end (docs/the-loop.md)


def _keep_awake():
    """macOS: stop idle sleep while the eval runs (`caffeinate -i`). A sleeping computer stops
    every engine, and the run looks hung. Returns the process, or None on other systems."""
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        return None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_eval(usecase: str, engine: str, case_ids=None, name=None, on_progress=None, holdout=False) -> dict:
    """Run the cases (all, or `case_ids`) and return the run dict. Blocks until all cases end.
    Cases with `"holdout": true` run only with holdout=True (or when you name them in case_ids).

    Raises CaseError / ValueError if the run cannot start (bad cases file, unknown engine or case id,
    n8n: schema not strict or the files not in n8n yet).
    """
    eng = engines.get(engine)
    if not cases.usecase_dir(usecase).is_dir():
        raise ValueError(f"use case {usecase!r} not found in usecases/")
    all_cases = cases.load_cases(usecase)
    todo = pick_cases(all_cases, case_ids, holdout)
    schema = cases.load_schema(usecase)
    extra = eng.before_run(usecase, schema)
    progress = on_progress or (lambda event: None)

    started = datetime.now()
    run_id = new_run_id(usecase, engine, started)
    run = {
        "run_id": run_id,
        "name": name or run_id,
        "usecase": usecase,
        "engine": engine,
        "started_at": started.astimezone().isoformat(timespec="seconds"),
        "ended_at": None,
        "holdout": bool(holdout),
        "summary": summarize([]),
        "cases": [],
        **extra,
        "agent_version": eng.version(usecase),
    }
    save_run(run)  # reserves the run id; ended_at = None means "still running"
    experiment = tracing.experiment_for_run(usecase, all_cases, run)
    run["experiment_url"] = experiment["url"] if experiment else None

    def one(case):
        progress({"case_id": case["id"], "status": "running"})
        res = run_case(usecase, engine, case, schema, run_id, run["name"], experiment)
        progress({"case_id": case["id"], "status": "done", "passed": res["passed"], "error": res["error"]})
        return res

    awake = _keep_awake()
    try:
        workers = min(config.settings().parallel, eng.max_parallel or sys.maxsize)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            run["cases"] = list(pool.map(one, todo))
        tracing.flush_within(30)
    finally:
        if awake:
            awake.terminate()
    run["summary"] = summarize(run["cases"])
    run["ended_at"] = _now()
    save_run(run)
    return run
