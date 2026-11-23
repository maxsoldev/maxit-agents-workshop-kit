"""Run files in `runs/<run_id>.json` (contracts §5)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from kit import config

SUMMARY_KEYS = ("run_id", "name", "usecase", "engine", "started_at", "ended_at", "summary")


def runs_dir() -> Path:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return config.RUNS_DIR


def new_run_id(uc: str, engine: str, now: datetime) -> str:
    """`<YYYYmmdd-HHMMSS>-<uc>-<engine>`; adds `-2`, `-3`, ... if that file is there already."""
    base = f"{now:%Y%m%d-%H%M%S}-{uc}-{engine}"
    run_id, n = base, 1
    while (runs_dir() / f"{run_id}.json").exists():
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def save_run(run: dict) -> Path:
    path = runs_dir() / f"{run['run_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_run(run_id: str) -> dict:
    path = runs_dir() / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"run {run_id} not found in runs/")
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(usecase: str | None = None) -> list[dict]:
    """Newest first; only the summary fields. Files that cannot be read are skipped."""
    runs = []
    for path in runs_dir().glob("*.json"):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if usecase is None or run.get("usecase") == usecase:
            row = {k: run.get(k) for k in SUMMARY_KEYS}
            row["case_ids"] = sorted(c.get("id", "") for c in run.get("cases") or [])  # to see other case sets
            runs.append(row)
    return sorted(runs, key=lambda r: (r.get("started_at") or "", r.get("run_id") or ""), reverse=True)
