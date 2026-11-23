"""`kit eval UC ENGINE`: run an eval and print one line per case and the summary."""
from __future__ import annotations

import sys
import threading

from kit.runner import CaseError, run_eval


def _label(check: dict) -> str:
    """`equals category: got X, expected Y` (the schema reason already names the field)."""
    where = "" if check["type"] == "schema" else f" {check['field']}"
    return f"{check['type']}{where}: {check['reason']}"


def main(usecase: str, engine: str, case_ids: list[str] | None = None, name: str | None = None) -> int:
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
        run = run_eval(usecase, engine, case_ids or None, name or None, on_progress)
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
    for c in run["cases"]:  # say once why the judge was skipped (for example: no key)
        why = next((k["reason"] for k in c["checks"] if k.get("skipped")), None)
        if why:
            print(f"   {why}")
            break
    print(f"Run file: runs/{run['run_id']}.json")
    return 0
