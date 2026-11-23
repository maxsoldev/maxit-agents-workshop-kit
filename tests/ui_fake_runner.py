"""A fake `kit.runner` for the UI tests and for a demo of the UI. It follows contracts §5.

`install()` puts it in sys.modules as `kit.runner`. Cases live in config.USECASES_DIR, runs in config.RUNS_DIR.
"""
from __future__ import annotations

import json
import sys
import time
import types
import zlib
from datetime import datetime

from kit import config

DELAY = 0.0  # seconds per case in run_eval; the demo sets it higher to show live progress


def load_cases(uc):
    path = config.USECASES_DIR / uc / "cases.jsonl"
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"cases.jsonl line {n}: {e.msg}") from e
    return out


def save_cases(uc, cases):
    lines = [json.dumps(c, ensure_ascii=False) for c in cases]
    (config.USECASES_DIR / uc / "cases.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_case(case):
    errors = []
    if not case.get("id"):
        errors.append("id: required")
    if not case.get("title"):
        errors.append("title: required")
    if not isinstance(case.get("input"), dict):
        errors.append("input: must be a JSON object")
    for i, c in enumerate(case.get("checks", []), 1):
        if c.get("type") not in ("equals", "in", "contains", "not_contains", "count", "any", "judge"):
            errors.append(f"check {i}: unknown type {c.get('type')!r}")
        if not c.get("field"):
            errors.append(f"check {i}: field is required")
        if c.get("type") == "judge" and not c.get("rubric"):
            errors.append(f"check {i}: judge needs a rubric")
        if c.get("type") == "in" and not isinstance(c.get("values"), list):
            errors.append(f"check {i}: in needs a list of values")
    return errors


def _passes(run_id, case_id):
    return zlib.crc32(f"{run_id}{case_id}".encode()) % 2 == 0


def run_eval(usecase, engine, case_ids=None, name=None, on_progress=None):
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{usecase}-{engine}"
    started = datetime.now().isoformat(timespec="seconds")
    results = []
    for c in load_cases(usecase):
        if case_ids and c["id"] not in case_ids:
            continue
        if on_progress:
            on_progress({"case_id": c["id"], "status": "running"})
        time.sleep(DELAY)
        ok = _passes(run_id, c["id"])
        checks = [{"type": k["type"], "field": k["field"], "passed": ok or n > 0,
                   "reason": "ok" if ok or n > 0 else f"got other_value, expected {c.get('expected', {}).get(k['field'])}"}
                  for n, k in enumerate(c.get("checks", []))]
        results.append({"id": c["id"], "title": c.get("title", ""), "passed": ok, "seconds": 1.5, "error": None,
                        "input": c.get("input", {}), "expected": c.get("expected", {}),
                        "output": {"category": "other_value", "reply": "Thank you."}, "checks": checks,
                        "trace_url": "http://localhost:5703/trace/x", "n8n_execution_url": None})
        if on_progress:
            on_progress({"case_id": c["id"], "status": "done", "passed": ok})
    by_check: dict = {}
    for r in results:
        for k in r["checks"]:
            pair = by_check.setdefault(k["type"], [0, 0])
            pair[0] += k["passed"]
            pair[1] += 1
    run = {"run_id": run_id, "name": name or run_id, "usecase": usecase, "engine": engine, "started_at": started,
           "ended_at": datetime.now().isoformat(timespec="seconds"),
           "summary": {"cases": len(results), "passed": sum(r["passed"] for r in results), "by_check": by_check},
           "cases": results}
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def load_run(run_id):
    path = config.RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(run_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(usecase=None):
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.RUNS_DIR.glob("*.json"), reverse=True)] \
        if config.RUNS_DIR.exists() else []
    return [{**{k: r.get(k) for k in ("run_id", "name", "usecase", "engine", "started_at", "ended_at", "summary")},
             "case_ids": sorted(c["id"] for c in r.get("cases", []))}
            for r in runs if usecase in (None, r.get("usecase"))]


def compare(a, b):
    return {}


def install():
    mod = types.ModuleType("kit.runner")
    for name in ("load_cases", "save_cases", "validate_case", "run_eval", "load_run", "list_runs", "compare"):
        setattr(mod, name, globals()[name])
    sys.modules["kit.runner"] = mod
    return mod
