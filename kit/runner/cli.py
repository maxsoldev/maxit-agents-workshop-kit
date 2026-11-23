"""`kit eval UC ENGINE`: run an eval and print one line per case and the summary."""

from __future__ import annotations

import sys
import threading

from kit.runner import CaseError, run_eval


def _label(check: dict) -> str:
    """`equals category: got X, expected Y` (the schema reason already names the field)."""
    where = "" if check["type"] == "schema" else f" {check['field']}"
    return f"{check['type']}{where}: {check['reason']}"


def main(
    usecase: str, engine: str, case_ids: list[str] | None = None, name: str | None = None, holdout: bool = False
) -> int:
    """Exit 0 when the run ran (also if cases fail); 1 only if it could not start."""
    lock, done = threading.Lock(), [0]

    def on_progress(event: dict) -> None:
        if event["status"] == "done":
            with lock:
                done[0] += 1
                mark = "pass" if event["passed"] else "fail"
                print(f"  [{done[0]}] {event['case_id']} {mark}", file=sys.stderr, flush=True)

    print(f"Running {usecase} on {engine} ...", file=sys.stderr, flush=True)
    try:
        run = run_eval(usecase, engine, case_ids or None, name or None, on_progress, holdout=holdout)
    except (CaseError, ValueError, OSError) as e:
        print(f"Cannot start the run: {e}", file=sys.stderr)
        return 1

    print()
    for c in run["cases"]:
        mark = "✓" if c["passed"] else "✗"
        line = f"{mark} {c['id']:<8} {c['title'][:50]:<50} {c['seconds']:>6.1f}s"
        if not c["passed"]:
            first = c["error"] or next((_label(k) for k in c["checks"] if k["passed"] is False), "")
            line += "  " + " ".join(first.split())[:140]
            if c.get("n8n_execution_url"):
                line += f"\n           n8n: {c['n8n_execution_url']}"
        print(line)
    s = run["summary"]
    by_check = "  ".join(f"{t} {p}/{n}" for t, (p, n) in s["by_check"].items())
    skipped = "  ".join(f"{n} {t} skipped" for t, n in s.get("skipped", {}).items())
    print(f"\n{s['passed']}/{s['cases']} cases passed   {by_check}" + (f"   ({skipped})" if skipped else ""))
    by_field = s.get("by_field") or {}
    if by_field:
        print("Per field: " + "  ".join(f"{k} {p}/{n}" for k, (p, n) in by_field.items() if k != "schema"))
    for c in run["cases"]:  # say once why the judge was skipped (for example: no key)
        why = next((k["reason"] for k in c["checks"] if k.get("skipped")), None)
        if why:
            print(f"   {why}")
            break
    print(f"Run file: runs/{run['run_id']}.json")
    return 0


def run_one(usecase: str, engine: str, case_id: str) -> int:
    """`kit run-case UC ENGINE CASE`: run one case and print what the engine got and gave, its log and the checks.
    No run file is written."""
    import json
    from datetime import datetime

    from kit.runner import _load_schema, load_cases, run_case

    try:
        case = next((c for c in load_cases(usecase) if c["id"] == case_id), None)
        if case is None:
            print(f"No case {case_id!r} in usecases/{usecase}/cases.jsonl", file=sys.stderr)
            return 1
        schema = _load_schema(usecase)
    except (CaseError, ValueError, OSError) as e:
        print(f"Cannot run the case: {e}", file=sys.stderr)
        return 1
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{usecase}-{engine}-run-case"
    print(f"Running {usecase}/{case_id} on {engine} ...", file=sys.stderr, flush=True)
    res = run_case(usecase, engine, case, schema, run_id, "run-case")
    from kit.runner import tracing

    tracing.flush()

    def block(title: str, value) -> None:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        print(f"\n===== {title} =====\n{text}")

    block("Engine input (plus output_schema)", {"case_id": case_id, "today": res.get("today"), "input": res["input"]})
    block("Output", res["output"])
    if res["error"]:
        block("Error", res["error"])
    meta = res.get("meta") or {}
    for t in meta.get("tool_calls") or []:
        block(f"Tool call {t['name']}", {"input": t.get("input"), "output": (t.get("output") or "")[:1500]})
    block("Engine log (stderr, last lines)", meta.get("stderr") or "(none)")
    if res.get("n8n_execution_url"):
        block("n8n execution", res["n8n_execution_url"])
    print("\n===== Checks =====")
    for c in res["checks"]:
        mark = "–" if c.get("skipped") else ("✓" if c["passed"] else "✗")
        print(f"{mark} {_label(c)}")
    print(
        f"\n{'PASS' if res['passed'] else 'FAIL'}  {res['seconds']} s"
        + (f"  trace: {res['trace_url']}" if res.get("trace_url") else "")
    )
    return 0
