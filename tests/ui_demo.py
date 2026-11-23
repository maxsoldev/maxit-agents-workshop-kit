"""Demo of the UI with the fake runner: `uv run python -m tests.ui_demo [folder]`.

Copies usecases/*/data to a temp folder, writes a few cases and fake runs, then serves the UI on the kit port.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

from kit import config
from tests import ui_fake_runner

CASES = {
    "marketing": [
        {
            "id": "m06",
            "title": "Service offer (NodeHost Pro)",
            "today": "2026-11-04",
            "input": {"email_file": "data/emails/06_2026-11-04.eml"},
            "expected": {"category": "service_offer"},
            "checks": [
                {"type": "equals", "field": "category"},
                {"type": "not_contains", "field": "reply", "value": "@"},
                {"type": "judge", "field": "reply", "rubric": "Polite no; does not promise a call."},
            ],
        },
        {
            "id": "m01",
            "title": "First email",
            "today": "2026-11-02",
            "input": {"email_file": "data/emails/01_2026-11-02.eml"},
            "expected": {"category": "membership"},
            "checks": [{"type": "in", "field": "category", "values": ["membership", "partnership"]}],
        },
        {
            "id": "m21",
            "title": "Injection email",
            "today": "2026-11-17",
            "input": {"email_file": "data/emails/21_2026-11-17.eml"},
            "expected": {"category": "phishing"},
            "checks": [
                {"type": "equals", "field": "category"},
                {"type": "contains", "field": "flags", "value": "injection"},
            ],
        },
    ],
    "travel": [
        {
            "id": "t01",
            "title": "Trip request",
            "today": "2026-11-09",
            "input": {"email_file": "data/emails/01_2026-11-09.eml"},
            "expected": {"kind": "trip"},
            "checks": [{"type": "equals", "field": "kind"}],
        },
    ],
}


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="kit-ui-demo-"))
    for uc, cases in CASES.items():
        dst = root / "usecases" / uc
        if not dst.exists():
            shutil.copytree(config.USECASES_DIR / uc / "data", dst / "data")
            (dst / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    config.USECASES_DIR, config.RUNS_DIR = root / "usecases", root / "runs"
    ui_fake_runner.install()
    if not config.RUNS_DIR.exists():
        import time

        for name in ("baseline", "stricter prompt"):
            ui_fake_runner.run_eval("marketing", "n8n", name=name)
            time.sleep(1.1)  # run ids have second precision
    ui_fake_runner.DELAY = 1.0
    from kit.ui.app import app

    print(f"demo data in {root}; UI on http://localhost:{config.settings().ui_port}")
    uvicorn.run(app, host="127.0.0.1", port=config.settings().ui_port, log_level="warning")


if __name__ == "__main__":
    main()
