"""The prompts of an n8n workflow as files (contracts §4).

`usecases/<uc>/n8n/workflow.json` holds a placeholder `{{PROMPT:<name>}}` where a node has a prompt; the text
is in `n8n/prompts/<name>.md`. `<name>` comes from the node name (`Decide and draft` -> `decide_and_draft`).
The system message is `<name>.md`, the user message (what the node gets) `<name>.input.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

from kit import config

PLACEHOLDER = re.compile(r"^(=?)\{\{PROMPT:([a-z0-9_.]+)\}\}$")


def workflow_file(uc: str) -> Path:
    """The n8n workflow of a use case (`Kit: <uc> workflow`, webhook path `<uc>`)."""
    return config.USECASES_DIR / uc / "n8n" / "workflow.json"


# node type (end of it) -> the prompt fields in `parameters`: (path, file suffix)
PROMPT_FIELDS = {
    "chainLlm": [(("messages", "messageValues", 0, "message"), ""), (("text",), ".input")],
    "agent": [(("options", "systemMessage"), ""), (("text",), ".input")],
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "node"


def prompt_base(node_name: str) -> str:
    """The file name (without `.md`) of a node's prompt."""
    return slug(node_name)


def _fields(node: dict):
    """(parent object, key, file suffix) of each prompt field the node has."""
    for end, fields in PROMPT_FIELDS.items():
        if node.get("type", "").endswith("." + end):
            for path, suffix in fields:
                obj = node.get("parameters", {})
                for key in path[:-1]:
                    try:
                        obj = obj[key]
                    except (KeyError, IndexError, TypeError):
                        obj = None
                        break
                if isinstance(obj, (dict, list)):
                    try:
                        if isinstance(obj[path[-1]], str):
                            yield obj, path[-1], suffix
                    except (KeyError, IndexError):
                        pass


def read_prompt(path: Path) -> str:
    """The prompt text of a file: the file without its last newline."""
    text = path.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def fill_prompts(value, folder: Path):
    """Put the text of `folder/<name>.md` in place of each `{{PROMPT:<name>}}` (a leading `=` stays)."""
    if isinstance(value, dict):
        return {k: fill_prompts(v, folder) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_prompts(v, folder) for v in value]
    if isinstance(value, str) and (m := PLACEHOLDER.match(value)):
        path = folder / f"{m.group(2)}.md"
        if not path.exists():
            raise ValueError(f"{m.group(0)}: the file n8n/prompts/{path.name} is missing")
        return m.group(1) + read_prompt(path)
    return value


def split_prompts(wf: dict) -> tuple[dict, dict[str, str]]:
    """The workflow with placeholders in its prompt fields, and {name: prompt text}. Changes `wf` in place."""
    prompts: dict[str, str] = {}
    for node in wf.get("nodes", []):
        base = prompt_base(node.get("name", ""))
        for obj, key, suffix in _fields(node):
            text = obj[key]
            if PLACEHOLDER.match(text):
                continue
            eq = "=" if text.startswith("=") else ""
            name = base + suffix
            if name in prompts:
                raise ValueError(f"two prompts would go to n8n/prompts/{name}.md: rename one of the nodes")
            prompts[name] = text[len(eq) :]
            obj[key] = f"{eq}{{{{PROMPT:{name}}}}}"
    return wf, prompts


def placeholder_names(value) -> set[str]:
    """The prompt names a workflow (as parsed JSON) points to."""
    if isinstance(value, dict):
        return set().union(*(placeholder_names(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholder_names(v) for v in value)) if value else set()
    if isinstance(value, str) and (m := PLACEHOLDER.match(value)):
        return {m.group(2)}
    return set()
