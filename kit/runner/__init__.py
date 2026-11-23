"""Eval runner: run the cases of a use case on one engine, check the results, save a run file.

from kit.runner import run_eval, list_runs, load_run, compare, n8n_warning
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from kit import config
from kit.runner import checks, engines, tracing
from kit.runner.cases import CaseError, engine_input, load_cases, save_cases, usecase_dir, validate_case
from kit.runner.engines import n8n_warning
from kit.runner.store import list_runs, load_run, new_run_id, save_run

__all__ = [
    "run_eval",
    "list_runs",
    "load_run",
    "compare",
    "load_cases",
    "save_cases",
    "validate_case",
    "CaseError",
    "n8n_warning",
    "agent_changed",
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load_schema(uc: str) -> dict:
    path = usecase_dir(uc) / "output.schema.json"
    if not path.exists():
        raise CaseError(f"usecases/{uc}/output.schema.json not found: every use case needs one")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        checks.jsonschema.validators.validator_for(schema).check_schema(schema)
    except (ValueError, checks.jsonschema.SchemaError) as e:
        raise CaseError(
            f"usecases/{uc}/output.schema.json is not a valid JSON Schema: {str(e).splitlines()[0]}"
        ) from None
    return schema


def _keep_awake():
    """macOS: stop idle sleep while the eval runs (`caffeinate -i`). A sleeping computer stops
    every engine, and the run looks hung. Returns the process, or None on other systems."""
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        return None


def case_hash(case: dict) -> str:
    """Short hash of what a case asks: today, input, expected, checks (not the title).
    Compare uses it to see that a case changed between two runs."""
    text = json.dumps(
        {k: case.get(k) for k in ("today", "input", "expected", "checks")}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def strict_problem(node, where: str = "the result") -> str | None:
    """Why a JSON Schema is not OpenAI-strict (the n8n model nodes send it as strict json_schema), or None.
    Strict: every object has all its properties in `required` and `additionalProperties: false`."""
    if isinstance(node, list):
        return next((p for x in node if (p := strict_problem(x, where))), None)
    if not isinstance(node, dict):
        return None
    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties") or {}
        missing = [k for k in props if k not in (node.get("required") or [])]
        if missing:
            return f"{where}: {', '.join(missing)} not in required"
        if node.get("additionalProperties") is not False:
            return f"{where}: no additionalProperties: false"
        return next(
            (p for k, v in props.items() if (p := strict_problem(v, k if where == "the result" else f"{where}.{k}"))),
            None,
        )
    return next((p for v in node.values() if (p := strict_problem(v, where))), None)  # items, anyOf, $defs


AGENT_FOLDERS = {"n8n": "n8n", "n8n-agent": "n8n", "claude": "claude", "python": "python"}


def agent_version(uc: str, engine: str) -> dict:
    """What the engine ran: {"hash", "git"}. n8n: the workflow with its prompts (the same hash as the sync
    state); claude, python: all files of the engine folder. `git` = the commit if that folder has no
    changes that are not committed, else None. Compare uses `hash` ("workflow changed")."""
    folder = usecase_dir(uc) / AGENT_FOLDERS.get(engine, engine)
    h = None
    if engine in ("n8n", "n8n-agent"):
        from kit import setup

        h = setup.files_hash(folder / ("agent.json" if engine == "n8n-agent" else "workflow.json"))
    if h is None:
        digest = hashlib.sha256()
        for p in sorted(folder.rglob("*")) if folder.is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store":
                digest.update(str(p.relative_to(folder)).encode() + b"\0" + p.read_bytes() + b"\0")
        h = digest.hexdigest()[:16]
    git = None
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", str(folder)], cwd=folder.parent, capture_output=True, text=True
        )
        if dirty.returncode == 0 and not dirty.stdout.strip():
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=folder.parent, capture_output=True)
            git = head.stdout.decode().strip() or None
    except OSError:
        pass
    return {"hash": h, "git": git}


def run_case(uc: str, engine: str, case: dict, schema: dict, run_id: str, run_name: str, experiment=None) -> dict:
    """Run one case and check it. Never raises: a crash is a failed case with `error`.
    `experiment` (from `tracing.start_experiment`): link the case trace to the Langfuse dataset run."""
    t0 = time.time()  # wall clock: on macOS, monotonic time stops while the computer sleeps
    res = {
        "id": case["id"],
        "title": case.get("title", ""),
        "today": case.get("today"),
        "case_hash": case_hash(case),
        "passed": False,
        "seconds": 0.0,
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
        inp = engine_input(uc, case)
        res["input"] = inp["input"]
        inp["output_schema"] = schema  # every engine sees the result shape (contracts §4)
        with tracing.case_trace(uc, engine, run_id, run_name, case, inp, experiment) as trace:
            res["trace_url"] = trace.url
            output, error, meta = engines.call_with_deadline(uc, engine, inp, trace.env(engine))
            res.update(output=output, error=error, meta=meta)
            res["n8n_execution_url"] = meta.pop("execution_url", None)
            trace.n8n_nodes(meta.pop("n8n_nodes", None) or [])
            trace.tool_calls(meta.get("tool_calls") or [])
            res["checks"] = checks.run_checks(case, output, schema, inp["input"], meta.get("tool_calls"))
            res["passed"] = error is None and all(c["passed"] is not False for c in res["checks"])  # skipped ok
            trace.finish(output, error, res["checks"], res["passed"])
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        res["passed"] = False
    res["seconds"] = round(time.time() - t0, 1)
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
    }


def _start_experiment(uc: str, cases: list[dict], run: dict) -> dict | None:
    """Langfuse on and not `make check` (a smoke test): sync the cases to the dataset `<uc>` and name the
    dataset run. Langfuse down or slow (20 s): None, and the run goes on with plain traces."""
    if not config.settings().langfuse_on or run["name"] == "check":
        return None
    try:
        return engines.with_deadline(lambda: tracing.start_experiment(uc, cases, run), 20)
    except Exception as e:
        print(f"WARN  Langfuse dataset sync failed, no experiment for this run: {e}", file=sys.stderr, flush=True)
        return None


def run_eval(usecase: str, engine: str, case_ids=None, name=None, on_progress=None, holdout=False) -> dict:
    """Run the cases (all, or `case_ids`) and return the run dict. Blocks until all cases end.
    Cases with `"holdout": true` run only with holdout=True (or when you name them in case_ids).

    Raises CaseError / ValueError if the run cannot start (bad cases file, unknown engine or case id).
    """
    if engine not in config.ALL_ENGINES:
        raise ValueError(f"unknown engine {engine!r}; use one of {', '.join(config.ALL_ENGINES)}")
    if not usecase_dir(usecase).is_dir():
        raise ValueError(f"use case {usecase!r} not found in usecases/")
    cases = all_cases = load_cases(usecase)
    if case_ids:
        unknown = [i for i in case_ids if i not in {c["id"] for c in cases}]
        if unknown:
            raise ValueError(f"unknown case id(s): {', '.join(unknown)}")
        cases = [c for c in cases if c["id"] in case_ids]
    elif not holdout:
        cases = [c for c in cases if not c.get("holdout")]  # kept aside for the end (docs/the-loop.md)
    schema = _load_schema(usecase)
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
    }
    if engine in ("n8n", "n8n-agent"):
        if problem := strict_problem(schema):
            raise CaseError(
                f"n8n needs a strict output.schema.json: {problem}. Put every field in required, and "
                'additionalProperties: false in every object; use "" (or []) for an empty value'
            )
        from kit import setup  # n8n -> files if only n8n changed; stops if the files changed (contracts §5)

        if synced := setup.sync_before_eval(usecase, engine):
            run["n8n_sync"] = synced
            print(f"NOTE  {synced}", file=sys.stderr, flush=True)
        if warning := n8n_warning(usecase, engine):
            run["warning"] = warning  # e.g. edits in n8n that are not published: the run used the published one
    run["agent_version"] = agent_version(usecase, engine)
    save_run(run)  # reserves the run id; ended_at = None means "still running"
    experiment = _start_experiment(usecase, all_cases, run)
    run["experiment_url"] = experiment["url"] if experiment else None

    def one(case):
        progress({"case_id": case["id"], "status": "running"})
        res = run_case(usecase, engine, case, schema, run_id, run["name"], experiment)
        progress({"case_id": case["id"], "status": "done", "passed": res["passed"], "error": res["error"]})
        return res

    awake = _keep_awake()
    try:
        workers = config.settings().parallel
        if engine == "claude":
            workers = min(workers, 4)  # each claude -p is a Node process on the user's subscription
        with ThreadPoolExecutor(max_workers=workers) as pool:
            run["cases"] = list(pool.map(one, cases))
        try:
            engines.with_deadline(tracing.flush, 30)  # a slow Langfuse must not hold the run
        except engines.Deadline:
            pass
    finally:
        if awake:
            awake.terminate()
    run["summary"] = summarize(run["cases"])
    run["ended_at"] = _now()
    save_run(run)
    return run


def _check_count(case: dict | None) -> tuple[int, int]:
    if not case:
        return 0, 0
    return sum(c["passed"] is True for c in case["checks"]), len(case["checks"])


def compare(run_id_a: str, run_id_b: str) -> dict:
    """How run B changed against run A, per case: better / worse / same (or new / removed).

    A case is better if it passes in B and not in A; if both are equal, more passed checks is better.
    `case_changed`: the case itself (input, expected, checks) is not the same in both runs.
    """
    a, b = load_run(run_id_a), load_run(run_id_b)
    cases_a = {c["id"]: c for c in a["cases"]}
    cases_b = {c["id"]: c for c in b["cases"]}
    rows, counts = [], {"better": 0, "worse": 0, "same": 0, "new": 0, "removed": 0}
    for cid in list(cases_a) + [i for i in cases_b if i not in cases_a]:
        ca, cb = cases_a.get(cid), cases_b.get(cid)
        if ca is None:
            change = "new"
        elif cb is None:
            change = "removed"
        else:
            key_a = (ca["passed"], _check_count(ca)[0])
            key_b = (cb["passed"], _check_count(cb)[0])
            change = "better" if key_b > key_a else "worse" if key_b < key_a else "same"
        counts[change] += 1
        changed = bool(ca and cb and ca.get("case_hash") and cb.get("case_hash") and ca["case_hash"] != cb["case_hash"])
        rows.append(
            {
                "id": cid,
                "title": (cb or ca).get("title", ""),
                "change": change,
                "case_changed": changed,
                "a_passed": ca["passed"] if ca else None,
                "b_passed": cb["passed"] if cb else None,
                "a_checks": list(_check_count(ca)),
                "b_checks": list(_check_count(cb)),
            }
        )
    keys = ("run_id", "name", "engine", "summary", "agent_version")
    out = {"a": {k: a.get(k) for k in keys}, "b": {k: b.get(k) for k in keys}, "counts": counts, "cases": rows}
    out["agent_changed"] = agent_changed(a, b)
    return out


def agent_changed(a: dict | None, b: dict | None) -> bool:
    """True if two runs of the same engine ran different agent files (n8n: "workflow changed")."""
    ha, hb = (((r or {}).get("agent_version") or {}).get("hash") for r in (a, b))
    return bool(ha and hb and ha != hb and a.get("engine") == b.get("engine"))
