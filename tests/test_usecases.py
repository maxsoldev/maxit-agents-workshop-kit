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
        if n["type"] == "n8n-nodes-base.httpRequest":
            assert n["parameters"]["specifyBody"] == "keypair" and "/call/" in n["parameters"]["url"], n["name"]


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


def owner_lists(uc: str, engine: str) -> dict[str, set[str]]:
    """The word lists the owner keeps in <engine>/rules/*.md: tables whose first column is a `word`
    (categories, flags, task kinds, status words, routes). Key: "<file>: <table head>"."""
    lists: dict[str, set[str]] = {}
    for f in sorted((USECASES / uc / engine / "rules").glob("*.md")):
        head = ""
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*`([a-z_]+)`\s*\|", line)
            if m:
                lists.setdefault(f"{engine}/rules/{f.name}: {head}", set()).add(m[1])
            elif line.startswith("|"):
                head = head or line
            else:
                head = ""
    return lists


def schema_enums(node) -> list[list]:
    if isinstance(node, dict):
        return ([node["enum"]] if "enum" in node else []) + [e for v in node.values() for e in schema_enums(v)]
    return [e for v in node for e in schema_enums(v)] if isinstance(node, list) else []


def agent_files(uc: str, engine: str) -> list[Path]:
    """The prompt files of one engine: where an owner's list must not be copied."""
    folder = USECASES / uc / engine
    if engine == "n8n":
        return sorted((folder / "prompts").glob("*.md"))
    return (
        [folder / "CLAUDE.md", *sorted((folder / ".claude").rglob("SKILL.md"))]
        if engine == "claude"
        else [folder / "agent.py"]
    )


@pytest.mark.parametrize("uc", UCS)
@pytest.mark.parametrize("engine", ENGINES)
def test_owner_lists_live_only_in_the_rules(uc, engine):
    """Lu's wish: the owner adds a category or a status word in the rules of their engine, with no code change.
    So no schema enum and no agent prompt repeats a list from the rules (one example word is fine)."""
    lists = owner_lists(uc, engine)
    assert lists, f"{uc}: no word tables in {engine}/rules/*.md"
    schemas = [json.loads((USECASES / uc / "output.schema.json").read_text())]
    for n in json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())["nodes"]:  # n8n model schemas
        fmt = n["parameters"].get("options", {}).get("textFormat", {}).get("textOptions") or {}
        if fmt.get("schema", "").startswith("{"):
            schemas.append(json.loads(fmt["schema"]))
    for where, words in lists.items():
        for enum in schema_enums(schemas):
            assert not words & set(enum), f"{uc}: schema enum {enum} repeats {where}"
        for f in agent_files(uc, engine):
            named = {w for w in words if re.search(rf"\b{w}\b", f.read_text(encoding="utf-8"))}
            assert len(named) <= 2, f"{f.relative_to(USECASES)} lists {sorted(named)} from {where}"


@pytest.mark.parametrize("uc", UCS)
def test_each_engine_reads_all_its_own_rules(uc):
    """Each engine has its own rules (<engine>/rules/*.md) and reads all of them on every case:
    n8n and Python by code, Claude in step 1 of CLAUDE.md. data/ has the inputs only, no rules."""
    assert not list((USECASES / uc / "data").glob("*.md")), f"{uc}: rules belong in <engine>/rules/, not data/"
    for engine in ENGINES:
        assert list((USECASES / uc / engine / "rules").glob("*.md")), f"{uc}: no {engine}/rules/*.md"
    nodes = {n["name"]: n for n in json.loads((USECASES / uc / "n8n" / "workflow.json").read_text())["nodes"]}
    body = {p["name"]: p["value"] for p in nodes["Read the rules"]["parameters"]["bodyParameters"]["parameters"]}
    assert body == {"usecase": uc, "engine": "n8n", "name": "*"}
    agent = (USECASES / uc / "python" / "agent.py").read_text()
    assert '"engine": "python", "name": "*"' in agent and "await owner_rules(client)" in agent
    claude = (USECASES / uc / "claude" / "CLAUDE.md").read_text()
    assert f'1. Call `rules_get(usecase="{uc}", engine="claude", name="*")`' in claude
