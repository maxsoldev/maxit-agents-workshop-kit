"""Call one engine with one case (contracts §4). One module per engine owns its quirks:
`n8n`, `claude`, `python`. This module is the one door the runner uses.

An engine call returns `(output, error, meta)` (`common.Answer`) and never raises for a problem of the agent:
error = a text that says what to do.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from kit import config
from kit.runner.deadline import Deadline, with_deadline
from kit.runner.engines import claude, n8n, python
from kit.runner.engines.common import TIMEOUTS, Answer, engine_env

__all__ = ["TIMEOUTS", "GRACE_S", "Answer", "call", "call_with_deadline", "get", "engine_env", "n8n_warning"]

GRACE_S = 10  # the hard deadline of a case = engine timeout + this


@dataclass(frozen=True)
class Engine:
    """What the runner knows about an engine. `call(uc, engine_input, extra_env)` runs one case."""

    name: str
    call: Callable[[str, dict, dict], Answer]
    folder: str  # usecases/<uc>/<folder>: the agent files (agent_version)
    max_parallel: int | None = None  # cases at the same time, at most (else KIT_PARALLEL)
    otel: bool = False  # gets the OTel env of the case trace (Claude Code sends its own spans)

    @property
    def is_n8n(self) -> bool:
        return self.name == "n8n"

    def before_run(self, uc: str, schema: dict) -> dict:
        """Checks before an eval; returns extra keys for the run file. Raises if the run cannot start."""
        return n8n.before_run(uc, schema) if self.is_n8n else {}

    def version(self, uc: str) -> dict:
        """What the engine ran: {"hash", "git"}. n8n: the workflow (the hash of the sync state) plus its
        business rules files (n8n/*.md); claude, python: all files of the engine folder. `git` = the
        commit if that folder has no changes that are not committed, else None. Compare uses `hash`."""
        folder = config.USECASES_DIR / uc / self.folder
        h = n8n.version_hash(uc) if self.is_n8n else None
        if h is not None:  # n8n: a rules file edit is not an n8n sync change, but the agent changed
            rules = "".join(f"{p.name}\0{p.read_text(encoding='utf-8')}\0" for p in config.rule_files(uc, "n8n"))
            return {"hash": hashlib.sha256((h + rules).encode()).hexdigest()[:16], "git": _clean_commit(folder)}
        return {"hash": _digest(folder), "git": _clean_commit(folder)}


def _digest(folder) -> str:
    """A short hash of all files in `folder` (names and bytes)."""
    digest = hashlib.sha256()
    for p in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store":
            digest.update(str(p.relative_to(folder)).encode() + b"\0" + p.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def _clean_commit(folder) -> str | None:
    """The short HEAD commit if git has no uncommitted changes in `folder`, else None."""
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", str(folder)], cwd=folder.parent, capture_output=True, text=True
        )
        if dirty.returncode == 0 and not dirty.stdout.strip():
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=folder.parent, capture_output=True)
            return head.stdout.decode().strip() or None
    except OSError:
        pass
    return None


ENGINES = {
    "n8n": Engine("n8n", n8n.call, "n8n"),
    "claude": Engine("claude", claude.call, "claude", max_parallel=4, otel=True),  # each one is a Node process
    "python": Engine("python", python.call, "python"),
}


def get(name: str) -> Engine:
    """The engine `name`; ValueError if there is none."""
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r}; use one of {', '.join(config.ENGINES)}")
    return ENGINES[name]


def call(uc: str, engine: str, engine_input: dict, extra_env: dict | None = None) -> Answer:
    """Run `engine` on one case. `extra_env` adds env variables (tracing)."""
    if engine not in ENGINES:
        return None, f"unknown engine {engine!r}; use one of {', '.join(config.ENGINES)}", {}
    return ENGINES[engine].call(uc, engine_input, extra_env or {})


def call_with_deadline(uc: str, engine: str, engine_input: dict, extra_env: dict | None = None) -> Answer:
    """Like `call`, but never waits longer than the engine timeout + GRACE_S.
    The subprocess engines also kill their own process group at the timeout."""
    limit = TIMEOUTS.get(engine, 120)
    try:
        return with_deadline(lambda: call(uc, engine, engine_input, extra_env), limit + GRACE_S)
    except Deadline:
        return None, f"{engine} did not finish in {limit} s (hard deadline): the runner stopped waiting", {}


def n8n_warning(uc: str) -> str | None:
    """A warning if the n8n workflow is not in sync with the files or has unpublished edits (contracts §5)."""
    return n8n.warning(uc)
