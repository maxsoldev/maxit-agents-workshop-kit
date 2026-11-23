"""The n8n workflow of a use case as a file (contracts §4): `usecases/<uc>/n8n/workflow.json`.

The prompts are not in it: each AI step reads its business rules file (`n8n/<step>.md`) from the tools server on
every case, so a rules edit needs no sync. The sync (`make n8n-push`, `make n8n-pull`) is for the graph only.
"""

from __future__ import annotations

import re
from pathlib import Path

from kit import config


def workflow_file(uc: str) -> Path:
    """The n8n workflow of a use case (`Kit: <uc> workflow`, webhook path `<uc>`)."""
    return config.USECASES_DIR / uc / "n8n" / "workflow.json"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "node"
