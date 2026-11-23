"""Langfuse tracing: one trace per case, cases as a dataset, an eval run as a dataset run (contracts §6).
Does nothing when `settings().langfuse_on` is false."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
from contextlib import contextmanager, nullcontext

from kit.config import ROOT, settings
from kit.runner.deadline import Deadline, with_deadline

_client = None
_lock = threading.Lock()


def client():
    """One Langfuse client for the process, made on first use."""
    global _client
    with _lock:
        if _client is None:
            from langfuse import Langfuse

            s = settings()
            _client = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key, host=s.langfuse_host)
        return _client


class NoTrace:
    """The case trace when Langfuse is off: every method does nothing."""

    url = None

    def otel_env(self) -> dict:
        return {}

    def finish(self, output, error, checks, passed) -> None:
        pass

    def tool_calls(self, calls: list[dict]) -> None:
        pass

    def n8n_nodes(self, nodes: list[dict]) -> None:
        pass


class CaseTrace(NoTrace):
    def __init__(self, lf, span, uc: str):
        self.lf, self.span, self.uc = lf, span, uc
        try:
            self.url = lf.get_trace_url(trace_id=span.trace_id)
        except Exception:
            self.url = None

    def otel_env(self) -> dict:
        """OTel env for Claude Code, so its spans nest under this case trace."""
        s = settings()
        auth = base64.b64encode(f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()).decode()
        return {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            # content in the spans: tool input (DETAILS) and output (CONTENT), the prompt and the answers
            "OTEL_LOG_TOOL_DETAILS": "1",
            "OTEL_LOG_TOOL_CONTENT": "1",
            "OTEL_LOG_USER_PROMPTS": "1",
            "OTEL_LOG_ASSISTANT_RESPONSES": "1",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{s.langfuse_host}/api/public/otel/v1/traces",
            "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic {auth}",
            "OTEL_RESOURCE_ATTRIBUTES": f"service.name=claude-code-{self.uc}",
            "TRACEPARENT": f"00-{self.span.trace_id}-{self.span.id}-01",
        }

    def tool_calls(self, calls: list[dict]) -> None:
        """One TOOL span per tool call (name, input, output), read from Claude's stream-json.
        Claude's own OTel tool spans have no input, so these are the ones to read."""
        try:
            for c in calls:
                self.span.start_observation(
                    name=f"tool:{c['name']}", as_type="tool", input=c.get("input"), output=c.get("output")
                ).end()
        except Exception:
            pass  # tracing must never break a run

    def n8n_nodes(self, nodes: list[dict]) -> None:
        """One observation per n8n node run (`n8n_execution.nodes`), with the start and end time of the execution.
        Sub-nodes (model, tool, parser) nest under their AI node; model runs are generations with tokens."""
        made: dict[int, object] = {}

        def make(i: int):
            if i not in made:
                n = nodes[i]
                parent = self.span if n["parent"] is None else make(n["parent"])
                extra = {"model": n["model"], "usage_details": n["usage"]} if n["kind"] == "generation" else {}
                meta = {"n8n_node_type": n["node_type"]} | ({"branch": n["branch"]} if n["branch"] is not None else {})
                o = parent.start_observation(
                    name=n["name"], as_type=n["kind"], input=n["input"], output=n["output"], metadata=meta,
                    level="ERROR" if n["error"] else None, status_message=n["error"], **extra,
                )  # fmt: skip
                o._otel_span._start_time = int(n["start"] * 1e6)  # the SDK has no start time argument (ms -> ns)
                made[i] = o
            return made[i]

        try:
            for i in range(len(nodes)):
                make(i)
            for i, o in made.items():
                o.end(end_time=int(nodes[i]["end"] * 1e6))
        except Exception:
            pass  # tracing must never break a run

    def finish(self, output, error, checks, passed) -> None:
        """Write the output and one score per check (1/0) plus `passed`."""
        try:
            self.span.update(
                output=output if error is None else {"error": error, "output": output},
                level="ERROR" if error else "DEFAULT",
            )
            for c in checks:
                if c.get("skipped"):
                    continue
                name = c["type"] + (f".{c['field']}" if c["field"] else "")
                self.span.score_trace(name=name, value=1 if c["passed"] else 0, comment=c["reason"])
            self.span.score_trace(name="passed", value=1 if passed else 0)
        except Exception:
            pass  # tracing must never break a run


def item_id(uc: str, case_id: str) -> str:
    """Dataset item id of a case. Langfuse item ids are unique in the project, so the use case is in it."""
    return f"{uc}-{case_id}"


def _item(case: dict) -> dict:
    meta = {"title": case.get("title", ""), "today": case.get("today"), "checks": case.get("checks", [])}
    meta["holdout"] = bool(case.get("holdout"))
    return {"input": case.get("input", {}), "expected_output": case.get("expected", {}), "metadata": meta}


def sync_dataset(uc: str, cases: list[dict]) -> str:
    """Upsert one item per case into the dataset `<uc>` (cases.jsonl is the source); archive items of
    removed cases. Writes only what changed. Returns the dataset id."""
    lf = client()
    try:
        ds = lf.get_dataset(uc)
        old, dataset_id = {i.id: i for i in ds.items}, ds.id
    except Exception:  # not found
        old, dataset_id = {}, lf.create_dataset(name=uc, description=f"Kit cases of usecases/{uc}/cases.jsonl").id
    for case in cases:
        new, was = _item(case), old.pop(item_id(uc, case["id"]), None)
        if was and was.status == "ACTIVE" and (was.input, was.expected_output, was.metadata) == tuple(new.values()):
            continue
        lf.create_dataset_item(dataset_name=uc, id=item_id(uc, case["id"]), status="ACTIVE", **new)
    for was in old.values():
        if was.status == "ACTIVE" and was.id.startswith(f"{uc}-"):
            lf.create_dataset_item(
                dataset_name=uc, id=was.id, status="ARCHIVED", input=was.input,
                expected_output=was.expected_output, metadata=was.metadata,
            )  # fmt: skip
    return dataset_id


def start_experiment(uc: str, cases: list[dict], run: dict) -> dict:
    """Sync the dataset and name the dataset run (experiment) of this eval run. Returns
    `{id, name, dataset_id, metadata, url}`; `case_trace` links each case trace to its item."""
    s = settings()
    dataset_id = sync_dataset(uc, cases)
    lf = client()
    exp_id = lf.create_trace_id(seed=run["run_id"])
    project_id = lf.api.projects.get().data[0].id
    name = run["run_id"] if run["name"] == run["run_id"] else f"{run['name']} ({run['run_id']})"
    return {
        "id": exp_id,
        "name": name,
        "dataset_id": dataset_id,
        "metadata": {"engine": run["engine"], "model": s.model, "judge_model": s.judge_model, "git": _git_commit()},
        "url": f"{s.langfuse_host}/project/{project_id}/experiments/results?baseline={exp_id}",
    }


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _in_experiment(experiment: dict | None, span, uc: str, case: dict):
    """Mark this case trace as the run item of `case` in the experiment (Langfuse experiment attributes,
    the same ones that `Langfuse.run_experiment` sets). No experiment: does nothing."""
    if not experiment:
        return nullcontext()
    from langfuse._client.attributes import LangfuseOtelSpanAttributes as A
    from langfuse._client.propagation import _propagate_attributes

    span._otel_span.set_attribute(A.EXPERIMENT_ITEM_EXPECTED_OUTPUT, json.dumps(case.get("expected", {})))
    attrs = {
        "experiment_id": experiment["id"],
        "experiment_name": experiment["name"],
        "experiment_metadata": json.dumps(experiment["metadata"]),
        "experiment_dataset_id": experiment["dataset_id"],
        "experiment_item_id": item_id(uc, case["id"]),
        "experiment_item_metadata": json.dumps(_item(case)["metadata"]),
        "experiment_item_root_observation_id": span.id,
    }
    return _propagate_attributes(experiment=attrs)


@contextmanager
def case_trace(uc: str, engine: str, run_id: str, run_name: str, case: dict, engine_input: dict, experiment=None):
    """Context for one case. Yields an object with `.url`, `.otel_env()`, `.tool_calls(...)`, `.n8n_nodes(...)`
    and `.finish(...)` (a NoTrace when Langfuse is off).
    With `experiment` (from `start_experiment`), the trace is also the case's item in that dataset run."""
    if not settings().langfuse_on:
        yield NoTrace()
        return
    from langfuse import propagate_attributes

    lf = client()
    name = f"{uc}/{case['id']}"
    with lf.start_as_current_observation(name=name, input=engine_input) as span:
        with propagate_attributes(trace_name=name, session_id=run_id, tags=[uc, engine, run_name]):
            try:
                ctx = _in_experiment(experiment, span, uc, case)
            except Exception:
                ctx = nullcontext()  # tracing must never break a run
            with ctx:
                yield CaseTrace(lf, span, uc)


def experiment_for_run(uc: str, cases: list[dict], run: dict) -> dict | None:
    """The experiment of an eval run, or None: Langfuse off, a `make check` run (a smoke test), or Langfuse
    down or slow (20 s; then a WARN line, and the run goes on with plain traces)."""
    if not settings().langfuse_on or run["name"] == "check":
        return None
    try:
        return with_deadline(lambda: start_experiment(uc, cases, run), 20)
    except Exception as e:
        print(f"WARN  Langfuse dataset sync failed, no experiment for this run: {e}", file=sys.stderr, flush=True)
        return None


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass


def flush_within(seconds: float = 30) -> None:
    """Flush, but a slow Langfuse must not hold the run."""
    try:
        with_deadline(lambda: flush(), seconds)
    except Deadline:
        pass
