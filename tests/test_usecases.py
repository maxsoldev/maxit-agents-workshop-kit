"""Checks that every use case folder is complete and every case in cases.jsonl is valid (contracts §2, §3)."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import jsonschema
import pytest

USECASES = Path(__file__).resolve().parent.parent / "usecases"
UCS = sorted(p.name for p in USECASES.iterdir() if (p / "cases.jsonl").exists())
CHECK_KEYS = {
    "equals": set(),
    "in": {"values"},
    "contains": {"value"},
    "not_contains": {"value"},
    "judge": {"rubric"},
    "count": set(),
    "any": set(),
}
MISSING = object()


def get_path(data, path: str):
    """Follow a dotted path like `tasks.0.kind` into dicts and lists. MISSING if it is not there."""
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return MISSING
    return data


def load_cases(uc: str) -> list[dict]:
    lines = (USECASES / uc / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_there_are_use_cases():
    assert UCS, "no usecases/*/cases.jsonl found"


@pytest.mark.parametrize("uc", UCS)
def test_folder_has_all_parts(uc):
    folder = USECASES / uc
    for name in [
        "BRIEF.md",
        "output.schema.json",
        "n8n/workflow.json",
        "claude/CLAUDE.md",
        "claude/.mcp.json",
        "claude/.claude/settings.json",
        "python/agent.py",
    ]:
        assert (folder / name).exists(), f"{uc}: missing {name}"
    jsonschema.Draft202012Validator.check_schema(json.loads((folder / "output.schema.json").read_text()))


@pytest.mark.parametrize("uc", UCS)
def test_cases_are_valid(uc):
    cases = load_cases(uc)
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"{uc}: case ids are not unique"
    for c in cases:
        where = f"{uc}/{c['id']}"
        assert c.get("title"), f"{where}: no title"
        if "today" in c:
            dt.date.fromisoformat(c["today"])
        assert isinstance(c.get("input"), dict), f"{where}: input must be an object"
        for key, value in c["input"].items():
            if key.endswith("_file"):
                assert (USECASES / uc / value).is_file(), f"{where}: file not found: {value}"
        assert isinstance(c.get("expected"), dict), f"{where}: expected must be an object"
        assert c.get("checks"), f"{where}: no checks"
        for chk in c["checks"]:
            assert chk.get("type") in CHECK_KEYS, f"{where}: unknown check type {chk.get('type')}"
            assert chk.get("field"), f"{where}: check without field"
            for key in CHECK_KEYS[chk["type"]]:
                assert key in chk, f"{where}: {chk['type']} check needs '{key}'"
            if chk["type"] == "equals" and "value" not in chk:
                assert get_path(c["expected"], chk["field"]) is not MISSING, (
                    f"{where}: expected has no '{chk['field']}'"
                )


@pytest.mark.parametrize("uc", UCS)
def test_n8n_workflow_shape(uc):
    wf = json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())
    assert wf["name"] == f"Kit: {uc} workflow"
    hooks = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook"]
    assert len(hooks) == 1 and hooks[0]["parameters"]["path"] == uc
    assert any(n["type"] == "n8n-nodes-base.respondToWebhook" for n in wf["nodes"])
    # no AI Agent node: code decides the path
    assert not any(n["type"] == "@n8n/n8n-nodes-langchain.agent" for n in wf["nodes"])


@pytest.mark.parametrize("uc", UCS)
def test_n8n_workflow_files_have_no_pin_data(uc):
    """The files define the pipeline, not one case. The data of a case is in its n8n execution."""
    text = (USECASES / uc / "n8n" / "workflow.json").read_text()
    assert "pinData" not in json.loads(text) and "pinned" not in text.lower()


@pytest.mark.parametrize("uc", UCS)
def test_n8n_nodes_are_readable(uc):
    """Every main node has a note on the canvas; tool calls send fields, not a JSON expression."""
    wf = json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())
    for n in wf["nodes"]:
        if n["name"] in wf["connections"] or n["type"] == "n8n-nodes-base.respondToWebhook":
            if "ai_languageModel" not in wf["connections"].get(n["name"], {}):  # not a model sub-node
                assert n.get("notes") and n.get("notesInFlow"), n["name"]
        if n["type"] == "n8n-nodes-base.httpRequest":  # a tool call sends fields; a rules read is a plain GET
            url = n["parameters"]["url"]
            assert "/rules/" in url or (n["parameters"]["specifyBody"] == "keypair" and "/call/" in url), n["name"]


@pytest.mark.parametrize("uc", UCS)
def test_n8n_ai_steps_need_no_parser(uc):
    """n8n's Structured Output Parser wants {"output": {...}} and fails on a good unwrapped answer (m21).
    So each AI step: the model node forces a JSON schema, a Code node after the chain reads the text."""
    wf = json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())
    by = {n["name"]: n for n in wf["nodes"]}
    links = wf["connections"]
    chains = [n for n in wf["nodes"] if n["type"] == "@n8n/n8n-nodes-langchain.chainLlm"]
    assert chains and not any(n["type"].endswith("outputParserStructured") for n in wf["nodes"])
    for chain in chains:
        model = next(
            by[m] for m, out in links.items() if out.get("ai_languageModel", [[{}]])[0][0].get("node") == chain["name"]
        )
        fmt = model["parameters"]["options"]["textFormat"]["textOptions"]
        assert model["parameters"]["responsesApiEnabled"] and fmt["type"] == "json_schema", model["name"]
        reader = by[links[chain["name"]]["main"][0][0]["node"]]
        assert reader["type"] == "n8n-nodes-base.code" and "JSON.parse" in reader["parameters"]["jsCode"]


def _strict_ok(schema: dict, where: str) -> None:
    """OpenAI strict schema: each object requires all its properties and has additionalProperties false."""
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False, f"{where}: additionalProperties must be false"
        assert sorted(schema.get("required", [])) == sorted(schema.get("properties", {})), f"{where}: all required"
    for key, sub in (schema.get("properties") or {}).items():
        _strict_ok(sub, f"{where}.{key}")
    if isinstance(schema.get("items"), dict):
        _strict_ok(schema["items"], f"{where}[]")


@pytest.mark.parametrize("uc", UCS)
def test_schemas_are_openai_strict(uc):
    """Real OpenAI rejects a non-strict schema in n8n's JSON Schema format (400 Bad request; a gateway may not)."""
    _strict_ok(json.loads((USECASES / uc / "output.schema.json").read_text()), f"{uc}/output.schema.json")
    for f in (USECASES / uc / "n8n").glob("*.json"):
        for n in json.loads(f.read_text())["nodes"]:
            opts = n["parameters"].get("options", {}).get("textFormat", {}).get("textOptions") or {}
            if opts.get("schema") and not opts["schema"].startswith("="):
                _strict_ok(json.loads(opts["schema"]), f"{f.name}:{n['name']}")


ENGINES = ("n8n", "claude", "python")


def rule_files(uc: str, engine: str) -> list[Path]:
    from kit import config

    return config.rule_files(uc, engine)


def owner_lists(uc: str, engine: str) -> dict[str, set[str]]:
    """The word lists in the business rules files of an engine: list items that start with a `word`
    (categories, flags, kinds). Key: "<file>: <heading>"."""
    lists: dict[str, set[str]] = {}
    for f in rule_files(uc, engine):
        head = ""
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"- `([a-z_]+)`:", line)
            if line.startswith("#"):
                head = line
            elif m:
                lists.setdefault(f"{engine}/{f.name}: {head}", set()).add(m[1])
    return lists


def schema_enums(node) -> list[list]:
    if isinstance(node, dict):
        return ([node["enum"]] if "enum" in node else []) + [e for v in node.values() for e in schema_enums(v)]
    return [e for v in node for e in schema_enums(v)] if isinstance(node, list) else []


@pytest.mark.parametrize("uc", UCS)
@pytest.mark.parametrize("engine", ENGINES)
def test_owner_lists_live_only_in_the_rules(uc, engine):
    """Lu's wish: the owner adds a category or a kind in the business rules, with no code change.
    So no schema (case schema or an n8n model node) repeats a list from the rules as an enum."""
    lists = owner_lists(uc, engine)
    assert lists, f"{uc}: no word lists in the business rules of {engine}"
    schemas = [json.loads((USECASES / uc / "output.schema.json").read_text())]
    for n in json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())["nodes"]:  # n8n model schemas
        fmt = n["parameters"].get("options", {}).get("textFormat", {}).get("textOptions") or {}
        if fmt.get("schema", "").startswith("{"):
            schemas.append(json.loads(fmt["schema"]))
    for where, words in lists.items():
        for enum in schema_enums(schemas):
            assert not words & set(enum), f"{uc}: schema enum {enum} repeats {where}"


@pytest.mark.parametrize("uc", UCS)
def test_each_engine_has_one_rules_file_read_on_every_case(uc):
    """One business rules file per engine (n8n: one per AI step), read on every case: Claude Code loads CLAUDE.md,
    agent.py reads prompt.md, an n8n AI node gets its file from Read the rules. No rules/ or prompts/ folders."""
    for engine in ENGINES:
        assert rule_files(uc, engine), f"{uc}: no business rules file for {engine}"
        assert not (USECASES / uc / engine / "rules").exists() and not (USECASES / uc / engine / "prompts").exists()
    nodes = {n["name"]: n for n in json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())["nodes"]}
    url = nodes["Read the rules"]["parameters"]["url"]
    name = url.rsplit("/", 1)[-1]
    assert url == f"{{{{KIT_TOOLS_URL}}}}/rules/{uc}/n8n/{name}" and (USECASES / uc / "n8n" / name).is_file()
    ai = [n for n in nodes.values() if n["type"].endswith(".chainLlm")]
    assert [n["parameters"]["messages"]["messageValues"][0]["message"] for n in ai] == [
        "={{ $('Read the rules').item.json.text }}"
    ]
    assert 'PROMPT_FILE = HERE / "prompt.md"' in (USECASES / uc / "python" / "agent.py").read_text()


@pytest.mark.parametrize("uc", UCS)
@pytest.mark.parametrize("engine", ENGINES)
def test_rules_have_no_tables(uc, engine):
    """A person edits the business rules: lists, one item per line, are easy; Markdown tables break easily."""
    for f in rule_files(uc, engine):
        lines = f.read_text(encoding="utf-8").splitlines()
        rows = [n for n, line in enumerate(lines, 1) if line.lstrip().startswith("|")]
        assert not rows, f"{uc}/{engine}/{f.name}: a table at line {rows[0]}; write it as a list"
