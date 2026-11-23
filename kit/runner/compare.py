"""Compare two run files, per case (contracts §5)."""

from __future__ import annotations

from kit.runner.store import load_run


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
