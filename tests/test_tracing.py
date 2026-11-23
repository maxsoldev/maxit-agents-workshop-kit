"""Langfuse dataset + experiment code with a fake Langfuse client (no network)."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from kit import runner
from kit.runner import tracing
from tests.test_runner import kit_env  # noqa: F401  (fake use case, Langfuse off)


class FakeLangfuse:
    def __init__(self):
        self.items: dict[str, NS] = {}
        self.writes: list[str] = []
        self.datasets: dict[str, str] = {}
        self.api = NS(projects=NS(get=lambda: NS(data=[NS(id="proj")])))

    def get_dataset(self, name):
        if name not in self.datasets:
            raise RuntimeError("not found")
        return NS(id=self.datasets[name], items=list(self.items.values()))

    def create_dataset(self, name, description=None):
        self.datasets[name] = f"ds-{name}"
        return NS(id=self.datasets[name])

    def create_dataset_item(self, dataset_name, id, status, input, expected_output, metadata):
        self.items[id] = NS(id=id, status=status, input=input, expected_output=expected_output, metadata=metadata)
        self.writes.append(f"{id} {status}")

    @staticmethod
    def create_trace_id(seed):
        return f"exp-{seed}"


@pytest.fixture
def lf(monkeypatch):
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "client", lambda: fake)
    return fake


def case(cid, **kw):
    return {"id": cid, "title": f"t{cid}", "today": "2026-11-04", "input": {"x": 1}, "expected": {"a": 1}} | kw


def test_sync_dataset_upserts_only_changes_and_archives_removed(lf):
    assert tracing.sync_dataset("mk", [case("m1"), case("m2", holdout=True)]) == "ds-mk"
    assert lf.writes == ["mk-m1 ACTIVE", "mk-m2 ACTIVE"]
    assert lf.items["mk-m2"].metadata == {"title": "tm2", "today": "2026-11-04", "checks": [], "holdout": True}
    lf.writes.clear()
    tracing.sync_dataset("mk", [case("m1"), case("m2", holdout=True)])
    assert lf.writes == []  # nothing changed: no writes
    tracing.sync_dataset("mk", [case("m1", expected={"a": 2})])
    assert lf.writes == ["mk-m1 ACTIVE", "mk-m2 ARCHIVED"]
    assert lf.items["mk-m2"].input == {"x": 1}  # archived, content kept
    lf.writes.clear()
    tracing.sync_dataset("mk", [case("m1", expected={"a": 2}), case("m2", holdout=True)])
    assert lf.writes == ["mk-m2 ACTIVE"]  # a case that comes back is active again


def test_start_experiment_names_and_url(lf, monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://lf:3000")
    run = {"run_id": "20261123-1200-mk-python", "name": "try 2", "engine": "python"}
    exp = tracing.start_experiment("mk", [case("m1")], run)
    assert exp["name"] == "try 2 (20261123-1200-mk-python)" and exp["dataset_id"] == "ds-mk"
    assert exp["url"] == "http://lf:3000/project/proj/experiments/results?baseline=exp-20261123-1200-mk-python"
    assert exp["metadata"]["engine"] == "python" and "judge_model" in exp["metadata"]
    run["name"] = run["run_id"]  # no name given: the run id only
    assert tracing.start_experiment("mk", [case("m1")], run)["name"] == run["run_id"]


@pytest.fixture
def lf_on(monkeypatch):
    """Langfuse on, but case traces are fakes that record the experiment they get."""
    monkeypatch.setenv("LANGFUSE", "local")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    seen = []

    @contextmanager
    def fake_trace(uc, engine, run_id, run_name, case, engine_input, experiment=None):
        seen.append((case["id"], experiment and experiment["id"]))
        yield tracing.NoTrace()

    monkeypatch.setattr(tracing, "case_trace", fake_trace)
    monkeypatch.setattr(tracing, "flush", lambda: None)
    return seen


def test_eval_links_every_case_to_the_experiment(lf, lf_on):
    run = runner.run_eval("fake", "python", case_ids=["f01", "f02"], name="x")
    assert run["experiment_url"].endswith(f"baseline=exp-{run['run_id']}")
    assert sorted(lf_on) == [("f01", f"exp-{run['run_id']}"), ("f02", f"exp-{run['run_id']}")]
    assert len(lf.items) == 4  # all cases of the use case are in the dataset, also the holdout one
    assert runner.load_run(run["run_id"])["experiment_url"] == run["experiment_url"]


def test_check_runs_make_no_experiment(lf, lf_on):
    run = runner.run_eval("fake", "python", case_ids=["f01"], name="check")
    assert run["experiment_url"] is None and lf.writes == [] and lf_on == [("f01", None)]


class FakeSpan:
    """Records start_observation calls; children are FakeSpans too."""

    def __init__(self, log, name="root"):
        self.log, self.name, self._otel_span = log, name, NS(_start_time=0)

    def start_observation(self, name, as_type, **kw):
        child = FakeSpan(self.log, name)
        self.log.append({"name": name, "parent": self.name, "type": as_type, "child": child} | kw)
        return child

    def end(self, end_time):
        self.end_time = end_time


def test_n8n_nodes_are_observations_with_execution_times(monkeypatch):
    monkeypatch.setenv("KIT_PRICES", "fast=1/2")
    log = []
    root = FakeSpan(log)
    root.trace_id = "t"
    trace = tracing.CaseTrace(NS(get_trace_url=lambda trace_id: None), root, "mk")
    model = {
        "name": "M",
        "node_type": "lm",
        "kind": "generation",
        "model": "fast",
        "usage": {"input": 3},
        "branch": None,
    }
    nodes = [
        model | {"start": 2, "end": 3, "input": ["System: x"], "output": "y", "error": None, "parent": 1},
        {"name": "Agent", "node_type": "x.agent", "kind": "agent", "model": None, "usage": None, "branch": None}
        | {"start": 1, "end": 5, "input": {}, "output": {}, "error": "boom", "parent": None},
    ]
    trace.n8n_nodes(nodes)
    agent, m = log  # the parent is made first
    assert (agent["name"], agent["parent"], agent["type"], agent["level"]) == ("Agent", "root", "agent", "ERROR")
    assert (m["parent"], m["type"], m["model"], m["usage_details"]) == ("Agent", "generation", "fast", {"input": 3})
    assert m["cost_details"] == {"total": 3e-6}  # from KIT_PRICES, so Langfuse has the run file's cost
    assert agent["child"]._otel_span._start_time == 1_000_000 and m["child"].end_time == 3_000_000
    trace.n8n_nodes([{"broken": True}])  # never raises


def test_langfuse_down_the_eval_still_runs(lf_on, monkeypatch, capsys):
    monkeypatch.setattr(tracing, "client", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
    run = runner.run_eval("fake", "python", case_ids=["f01"], name="x")
    assert run["ended_at"] and run["cases"][0]["passed"] and run["experiment_url"] is None
    assert "no experiment for this run" in capsys.readouterr().err
