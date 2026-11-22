"""The schema check and the 5 check types (contracts §3). Each returns {"type", "field", "passed", "reason"}."""
from __future__ import annotations

import json

import jsonschema

from kit.runner import judge

MISSING = object()


def get_field(obj, path: str):
    """Value at a dotted path (`options.0.price`), or MISSING."""
    for part in path.split(".") if path else []:
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return MISSING
    return obj


def short(value, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def result(kind: str, field: str, passed: bool, reason: str) -> dict:
    return {"type": kind, "field": field, "passed": passed, "reason": reason}


def schema_check(output, schema: dict) -> dict:
    if output is None:
        return result("schema", "", False, "no output")
    validator = jsonschema.validators.validator_for(schema)(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(output))
    if error is None:
        return result("schema", "", True, "matches output.schema.json")
    where = ".".join(str(p) for p in error.absolute_path) or "result"
    return result("schema", where, False, f"{where}: {error.message}")


def _contains(got, value) -> bool:
    if isinstance(got, str):
        return str(value).lower() in got.lower()
    if isinstance(got, list):
        return value in got
    return False


def run_check(check: dict, output, expected: dict, case_input: dict) -> dict:
    """Run one check on the output. Never raises: a problem is a failed check with a reason."""
    kind, field = check.get("type", "?"), check.get("field", "")
    if output is None:
        return result(kind, field, False, "no output")
    got = get_field(output, field)
    if got is MISSING:
        return result(kind, field, False, f"`{field}` is missing in the result")

    if kind == "equals":
        want = check["value"] if "value" in check else get_field(expected, field)
        if want is MISSING:
            return result(kind, field, False, f"no expected `{field}` in the case and no `value` in the check")
        ok = got == want
        return result(kind, field, ok, f"got {short(got)}" if ok else f"got {short(got)}, expected {short(want)}")
    if kind == "in":
        values = check.get("values", [])
        ok = got in values
        return result(kind, field, ok, f"got {short(got)}" if ok else f"got {short(got)}, expected one of {short(values)}")
    if kind in ("contains", "not_contains"):
        value = check.get("value")
        found = _contains(got, value)
        ok = found if kind == "contains" else not found
        verb = "has" if found else "does not have"
        return result(kind, field, ok, f"{verb} {short(value)}")
    if kind == "judge":
        minimum = check.get("min", 4)
        try:
            score, reason = judge.score(got, check.get("rubric", ""), case_input)
        except Exception as e:  # the judge must not stop the run
            return result(kind, field, False, f"judge failed: {e}")
        return result(kind, field, score >= minimum, f"score {score}/5 (min {minimum}): {reason}")
    return result(kind, field, False, f"unknown check type {kind!r}")


def run_checks(case: dict, output, schema: dict, case_input: dict) -> list[dict]:
    """The schema check first, then the case checks."""
    expected = case.get("expected", {})
    return [schema_check(output, schema)] + [run_check(c, output, expected, case_input) for c in case.get("checks", [])]
