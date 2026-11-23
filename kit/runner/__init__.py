"""Eval runner: run the cases of a use case on one engine, check the results, save a run file.

from kit.runner import run_eval, list_runs, load_run, compare, n8n_warning

Inside: `cases` (cases.jsonl, schema, engine input), `engines` (one module per engine), `checks` and `judge`,
`tracing` (Langfuse; does nothing when off), `run` (run_eval), `store` (run files), `compare`.
"""

from __future__ import annotations

from kit.runner.cases import CaseError, case_hash, load_cases, load_schema, save_cases, validate_case
from kit.runner.compare import agent_changed, compare
from kit.runner.engines import n8n_warning
from kit.runner.run import run_case, run_eval, summarize
from kit.runner.store import list_runs, load_run

__all__ = [
    "run_eval",
    "run_case",
    "summarize",
    "list_runs",
    "load_run",
    "compare",
    "agent_changed",
    "load_cases",
    "load_schema",
    "save_cases",
    "validate_case",
    "case_hash",
    "CaseError",
    "n8n_warning",
]
