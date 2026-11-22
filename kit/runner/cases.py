"""Eval cases: load, check, save `usecases/<uc>/cases.jsonl` (contracts §3)."""
from __future__ import annotations

import json
from pathlib import Path

from kit import config

CHECK_TYPES = {
    "equals": [],
    "in": ["values"],
    "contains": ["value"],
    "not_contains": ["value"],
    "judge": ["rubric"],
}


class CaseError(ValueError):
    """A case file or a case is not valid. The message says where and what."""


def usecase_dir(uc: str) -> Path:
    return config.USECASES_DIR / uc


def cases_path(uc: str) -> Path:
    return usecase_dir(uc) / "cases.jsonl"


def validate_case(case) -> list[str]:
    """Return the problems of one case as short sentences. Empty list = the case is valid."""
    if not isinstance(case, dict):
        return ["a case must be a JSON object"]
    errors = []
    if not isinstance(case.get("id"), str) or not case.get("id", "").strip():
        errors.append("`id` must be a non-empty text")
    if not isinstance(case.get("title", ""), str):
        errors.append("`title` must be a text")
    today = case.get("today")
    if today is not None and not (isinstance(today, str) and len(today) == 10 and today[4] == "-" and today[7] == "-"):
        errors.append("`today` must be an ISO date like 2026-11-04")
    if not isinstance(case.get("input", {}), dict):
        errors.append("`input` must be a JSON object")
    if not isinstance(case.get("expected", {}), dict):
        errors.append("`expected` must be a JSON object")
    checks = case.get("checks", [])
    if not isinstance(checks, list):
        return errors + ["`checks` must be a list"]
    for i, check in enumerate(checks, 1):
        if not isinstance(check, dict):
            errors.append(f"check {i}: must be a JSON object")
            continue
        kind = check.get("type")
        if kind not in CHECK_TYPES:
            errors.append(f"check {i}: unknown type {kind!r}; use one of {', '.join(CHECK_TYPES)}")
            continue
        if not isinstance(check.get("field"), str) or not check["field"]:
            errors.append(f"check {i} ({kind}): `field` is missing")
        for key in CHECK_TYPES[kind]:
            if key not in check:
                errors.append(f"check {i} ({kind}): `{key}` is missing")
        if kind == "in" and not isinstance(check.get("values", []), list):
            errors.append(f"check {i} (in): `values` must be a list")
    return errors


def load_cases(uc: str) -> list[dict]:
    """Read and check all cases of a use case. Raise CaseError with the line number on the first problem."""
    path = cases_path(uc)
    if not path.exists():
        raise CaseError(f"{path} not found")
    cases, seen = [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as e:
            raise CaseError(f"cases.jsonl line {n}: not valid JSON ({e.msg}, column {e.colno})") from None
        errors = validate_case(case)
        if errors:
            raise CaseError(f"cases.jsonl line {n}: " + "; ".join(errors))
        if case["id"] in seen:
            raise CaseError(f"cases.jsonl line {n}: id {case['id']!r} is used two times")
        seen.add(case["id"])
        cases.append(case)
    return cases


def save_cases(uc: str, cases: list[dict]) -> None:
    """Write all cases, one JSON per line, in the given order. Raise CaseError if a case is not valid."""
    seen = set()
    for i, case in enumerate(cases, 1):
        errors = validate_case(case)
        if errors:
            raise CaseError(f"case {i}: " + "; ".join(errors))
        if case["id"] in seen:
            raise CaseError(f"case {i}: id {case['id']!r} is used two times")
        seen.add(case["id"])
    text = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)
    cases_path(uc).write_text(text, encoding="utf-8")


def resolve_input(uc: str, inp: dict) -> dict:
    """Copy of `inp` where each `<name>_file` key also gets `<name>` = the file text."""
    out = dict(inp)
    for key, rel in inp.items():
        if key.endswith("_file") and isinstance(rel, str):
            path = usecase_dir(uc) / rel
            if not path.is_file():
                raise CaseError(f"input `{key}`: file {rel} not found in usecases/{uc}/")
            out[key[: -len("_file")]] = path.read_text(encoding="utf-8", errors="replace")
    return out


def engine_input(uc: str, case: dict) -> dict:
    """The JSON that an engine gets for one case (contracts §4)."""
    return {"case_id": case["id"], "today": case.get("today"), "input": resolve_input(uc, case.get("input", {}))}
