"""Checks that every use case folder is complete and every case in cases.jsonl is valid (contracts §2, §3)."""

from __future__ import annotations

import datetime as dt
import json
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
@pytest.mark.parametrize(
    "file,name,hook", [("workflow", "Kit: {uc} workflow", "{uc}"), ("agent", "Kit: {uc} agent (example)", "{uc}-agent")]
)
def test_n8n_workflow_shape(uc, file, name, hook):
    wf = json.loads((USECASES / uc / "n8n" / f"{file}.json").read_text())
    assert wf["name"] == name.format(uc=uc)
    hooks = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook"]
    assert len(hooks) == 1 and hooks[0]["parameters"]["path"] == hook.format(uc=uc)
    assert any(n["type"] == "n8n-nodes-base.respondToWebhook" for n in wf["nodes"])
    agent = any(n["type"] == "@n8n/n8n-nodes-langchain.agent" for n in wf["nodes"])
    assert agent == (file == "agent")  # the workflow has no AI Agent node: code decides the path


@pytest.mark.parametrize("uc", UCS)
@pytest.mark.parametrize("file", ["workflow", "agent"])
def test_n8n_pinned_sample_is_a_real_case(uc, file):
    """Case in has one pinned case, the same as the runner sends it, so Execute workflow in the editor
    shows real data on every node. (Production webhook runs ignore pinned data.)"""
    from kit.runner.cases import engine_input

    wf = json.loads((USECASES / uc / "n8n" / f"{file}.json").read_text())
    assert list(wf["pinData"]) == ["Case in"] and len(wf["pinData"]["Case in"]) == 1
    body = wf["pinData"]["Case in"][0]["json"]["body"]
    case = next(c for c in load_cases(uc) if c["id"] == body["case_id"])
    assert body == engine_input(uc, case) | {
        "output_schema": json.loads((USECASES / uc / "output.schema.json").read_text())
    }


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
