"""The schema check and the check types (contracts §3). Each returns {"type", "field", "passed", "reason"}."""

from __future__ import annotations

import json

import jsonschema

from kit.runner import judge
from kit.runner.engines import with_deadline

MISSING = object()
JUDGE_DEADLINE_S = 120  # hard limit for one judge check (the client: 45 s timeout, 1 retry)


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
    if value == "":
        return '"" (empty)'  # else an empty value is invisible in "got X, expected Y"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def result(kind: str, field: str, passed: bool | None, reason: str) -> dict:
    """passed None (with "skipped": True) = the check could not run; it does not count."""
    return {"type": kind, "field": field, "passed": passed, "reason": reason}


def schema_check(output, schema: dict) -> dict:
    if output is None:
        return result("schema", "", False, "no output")
    validator = jsonschema.validators.validator_for(schema)(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(output))
    if error is None:
        return result("schema", "", True, "matches output.schema.json")
    where = ".".join(str(p) for p in error.absolute_path) or "result"
    return result("schema", where, False, schema_words(error, where))


def schema_words(error, where: str) -> str:
    """A schema error in plain words: extra field, missing field, wrong value. Else the jsonschema text."""
    prefix = "" if where == "result" else f"{where}."
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extra = [k for k in error.instance if k not in (error.schema.get("properties") or {})]
        names = ", ".join(f"`{prefix}{k}`" for k in extra)
        return (
            f"the agent added a field that is not in the schema: {names}. Tell the agent to use only the "
            f"fields of output_schema, or add the field to output.schema.json"
        )
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = [k for k in error.validator_value if k not in error.instance]
        return f"the field {', '.join(f'`{prefix}{k}`' for k in missing)} is missing"
    if error.validator == "enum":
        return f"`{where}` must be one of {short(error.validator_value, 200)}, got {short(error.instance)}"
    if error.validator == "type":
        return f"`{where}` must be of type {error.validator_value}, got {short(error.instance)}"
    return f"{where}: {error.message}"


def _contains(got, value) -> bool:
    if isinstance(got, str):
        return str(value).lower() in got.lower()
    if isinstance(got, list):
        return value in got
    return False


def _text(value) -> str:
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).strip().lower()


def _item_misses(item, where: dict, contains: dict) -> list[str]:
    """`any` check: the keys of one item that do not match (empty list = the item matches).
    Text values: case-insensitive "contains" ("Elena" matches "Elena Popescu"), in `where` too.
    Other values (numbers, true/false) in `where` must be equal. Keys can be dotted paths."""
    misses = []
    for key, want in {**where, **contains}.items():
        got = get_field(item, key)
        if got is MISSING:
            misses.append(f"`{key}` is missing")
            continue
        exact = key in where and not isinstance(want, str) and key not in contains
        ok = _text(got) == _text(want) if exact else _text(want) in _text(got)
        if not ok:
            misses.append(f"`{key}` is {short(json.dumps(got, ensure_ascii=False))}")
    return misses


def run_check(check: dict, output, expected: dict, case_input: dict, tool_calls: list | None = None) -> dict:
    """Run one check on the output. Never raises: a problem is a failed check with a reason.
    `tool_calls` of the engine (if any) go to the judge: facts from tool results are allowed."""
    kind, field = check.get("type", "?"), check.get("field", "")
    if output is None:
        return result(kind, field, False, "no output")
    if "*" in field.split("."):
        return _every_item(check, output, expected, case_input, tool_calls)
    got = get_field(output, field)
    if got is MISSING and kind == "not_contains":  # nothing there, so the value is not there either
        return result(
            kind, field, True, f"`{field}` is missing or empty, so it does not have {short(check.get('value'))}"
        )
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
        return result(
            kind, field, ok, f"got {short(got)}" if ok else f"got {short(got)}, expected one of {short(values)}"
        )
    if kind in ("contains", "not_contains"):
        value = check.get("value")
        found = _contains(got, value)
        ok = found if kind == "contains" else not found
        verb = "has" if found else "does not have"
        return result(kind, field, ok, f"{verb} {short(value)}")
    if kind == "count":
        if not isinstance(got, list):
            return result(kind, field, False, f"`{field}` is not a list: {short(got)}")
        n, want = len(got), check.get("equals")
        ok = (want is None or n == want) and n >= check.get("min", n) and n <= check.get("max", n)
        limits = ", ".join(f"{k} {check[k]}" for k in ("equals", "min", "max") if k in check)
        return result(kind, field, ok, f"{n} items" + ("" if ok else f", expected {limits}"))
    if kind == "any":
        if not isinstance(got, list):
            return result(kind, field, False, f"`{field}` is not a list: {short(got)}")
        where, contains = check.get("where") or {}, check.get("contains") or {}
        misses = [_item_misses(item, where, contains) for item in got]
        want = short({**where, **{k: f"~{v}" for k, v in contains.items()}}, 200)
        hits = [i for i, m in enumerate(misses) if not m]
        if hits:
            return result(kind, field, True, f"item {hits[0] + 1} of {len(got)} matches {want}")  # 1 = the first
        if not got:
            return result(kind, field, False, f"`{field}` is empty, so no item matches {want}")
        best = min(range(len(got)), key=lambda i: len(misses[i]))  # the item that is closest
        return result(
            kind,
            field,
            False,
            f"no item of {len(got)} matches {want}; closest is item {best + 1}: " + ", ".join(misses[best]),
        )
    if kind == "judge":
        minimum = check.get("min", 4)
        try:
            score, reason = with_deadline(
                lambda: judge.score(got, check.get("rubric", ""), case_input, tool_calls), JUDGE_DEADLINE_S
            )
        except Exception as e:  # no key, API error, timeout: the judge did not run, so it neither passes nor fails
            return {**result(kind, field, None, f"judge skipped: {e}"), "skipped": True}
        return result(kind, field, score >= minimum, f"score {score}/5 (min {minimum}): {reason}")
    return result(kind, field, False, f"unknown check type {kind!r}")


def _every_item(check: dict, output, expected: dict, case_input: dict, tool_calls: list | None = None) -> dict:
    """`options.*.trains`: run the check on `trains` of every item of `options`; all items must pass.
    An empty list passes. The expected value of item i is at `options.i.trains` in `expected`."""
    kind, field = check.get("type", "?"), check["field"]
    parts = field.split(".")
    star = parts.index("*")
    base, rest = ".".join(parts[:star]), ".".join(parts[star + 1 :])
    items = get_field(output, base)
    if items is MISSING and kind == "not_contains":
        return result(kind, field, True, f"`{base}` is missing, so it does not have {short(check.get('value'))}")
    if not isinstance(items, list):
        return result(
            kind,
            field,
            False,
            f"`{base}` is missing in the result" if items is MISSING else f"`{base}` is not a list: {short(items)}",
        )
    for i, item in enumerate(items):
        exp = get_field(expected, f"{base}.{i}" if base else str(i))
        sub = run_check(
            {**check, "field": rest}, item, exp if not rest or isinstance(exp, dict) else {}, case_input, tool_calls
        )
        if sub["passed"] is None:
            return {**result(kind, field, None, sub["reason"]), "skipped": True}
        if not sub["passed"]:
            return result(kind, field, False, f"item {i + 1} of {len(items)}: {sub['reason']}")
    return result(kind, field, True, f"all {len(items)} items pass")


def run_checks(case: dict, output, schema: dict, case_input: dict, tool_calls: list | None = None) -> list[dict]:
    """The schema check first, then the case checks. Each case check result keeps its definition (`def`),
    so the run file shows what the check wanted at run time, also after the case changes."""
    expected = case.get("expected", {})
    return [schema_check(output, schema)] + [
        {**run_check(c, output, expected, case_input, tool_calls), "def": c} for c in case.get("checks", [])
    ]
