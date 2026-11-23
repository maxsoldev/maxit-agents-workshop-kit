"""Runner tests on the fake use case in tests/fixtures (fake engines, judge mocked, Langfuse off)."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kit import config
from kit import runner
from kit.runner import cases, checks, cli, engines, judge, store

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def kit_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USECASES_DIR", FIX / "usecases")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("LANGFUSE", "off")
    monkeypatch.setenv("PATH", f"{FIX / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(judge, "score", lambda value, rubric, inp, tools=None: (5, "polite") if "Thanks" in value
                        else (2, "rude"))


class FakeN8n(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/webhook/fake":
            self.send_response(404)
            self.end_headers()
            return
        FakeN8n.last = body
        inp = body["input"]
        if inp.get("mode") == "sleep":
            time.sleep(3)
        answer =[{"category": inp.get("want", "zzz"), "reply": inp.get("note", "hi"), "tags": ["z"]}]
        data = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def n8n(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeN8n)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("N8N_URL", f"http://127.0.0.1:{server.server_port}")
    yield server
    server.shutdown()


# cases


def test_load_cases_and_resolve_files():
    cs = cases.load_cases("fake")
    assert [c["id"] for c in cs] == ["f01", "f02", "f03", "f04"]
    inp = cases.engine_input("fake", cs[0])
    assert inp["case_id"] == "f01" and inp["today"] == "2026-11-04"
    assert inp["input"]["note"].startswith("Thanks") and inp["input"]["note_file"] == "data/note.txt"


def test_eml_files_are_decoded(tmp_path, monkeypatch):
    (tmp_path / "x" / "data").mkdir(parents=True)
    qp = ("From: Anna <a@x.example>\nDate: Tue, 03 Nov 2026 11:22:00 +0100\nSubject: =?utf-8?q?Gr=C3=BC=C3=9Fe?=\n"
          "Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: quoted-printable\n\nVielen Dank f=C3=BCr Ihre Mail.\n")
    html = ("From: B <b@x.example>\nSubject: Only HTML\nContent-Type: text/html; charset=utf-8\n\n"
            "<html><style>p {color: red}</style><body><p>Hello <b>team</b></p><p>Second line</p></body></html>\n")
    (tmp_path / "x" / "data" / "qp.eml").write_text(qp)
    (tmp_path / "x" / "data" / "html.eml").write_text(html)
    monkeypatch.setattr(config, "USECASES_DIR", tmp_path)
    out = cases.resolve_input("x", {"email_file": "data/qp.eml", "email_2_file": "data/html.eml"})
    assert out["email"].startswith("From: Anna <a@x.example>\nDate: Tue, 03 Nov 2026")
    assert "Subject: Grüße" in out["email"] and "Vielen Dank für Ihre Mail." in out["email"]
    assert "f=C3=BCr" in out["email_raw"]
    assert "Hello team\nSecond line" in out["email_2"] and "color" not in out["email_2"]


def test_bad_line_has_line_number(tmp_path, monkeypatch):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "cases.jsonl").write_text('{"id": "a", "checks": []}\n{"id": "b",\n', encoding="utf-8")
    monkeypatch.setattr(config, "USECASES_DIR", tmp_path)
    with pytest.raises(cases.CaseError, match="line 2"):
        cases.load_cases("x")


def test_validate_case():
    assert cases.validate_case({"id": "a", "checks": [{"type": "equals", "field": "x"}]}) == []
    errors = cases.validate_case({"id": "", "today": "4 Nov", "checks": [{"type": "magic"}, {"type": "in", "field": "x"}]})
    assert len(errors) == 4


def test_save_cases_keeps_order_and_utf8(tmp_path, monkeypatch):
    (tmp_path / "x").mkdir()
    monkeypatch.setattr(config, "USECASES_DIR", tmp_path)
    data = [{"id": "b", "title": "Grüße", "checks": []}, {"id": "a", "checks": []}]
    cases.save_cases("x", data)
    text = (tmp_path / "x" / "cases.jsonl").read_text(encoding="utf-8")
    assert "Grüße" in text and text.count("\n") == 2
    assert [c["id"] for c in cases.load_cases("x")] == ["b", "a"]
    with pytest.raises(cases.CaseError, match="two times"):
        cases.save_cases("x", data + [{"id": "a"}])


# checks


def test_checks_reasons():
    out = {"category": "a", "reply": "Thanks a lot", "options": [{"price": 10}], "flags": ["vip"]}
    exp = {"category": "b"}
    run = lambda c: checks.run_check(c, out, exp, {})  # noqa: E731
    assert run({"type": "equals", "field": "category"})["reason"] == "got a, expected b"
    assert run({"type": "equals", "field": "options.0.price", "value": 10})["passed"]
    assert run({"type": "in", "field": "category", "values": ["a", "c"]})["passed"]
    assert run({"type": "contains", "field": "reply", "value": "THANKS"})["passed"]
    assert run({"type": "contains", "field": "flags", "value": "vip"})["passed"]
    assert not run({"type": "not_contains", "field": "reply", "value": "thanks"})["passed"]
    assert "missing" in run({"type": "equals", "field": "nope"})["reason"]
    assert run({"type": "judge", "field": "reply", "rubric": "polite"})["reason"].startswith("score 5/5")
    assert not run({"type": "judge", "field": "reply", "rubric": "x", "min": 6})["passed"]


def test_count_and_any_checks():
    out = {"tasks": [{"kind": "trip", "event": "E-Science-Tage, Heidelberg", "who": {"name": "Jonas"}},
                     {"kind": "Leave", "event": "Holiday", "who": {"name": "Elena"}}], "reply": "x"}
    run = lambda c: checks.run_check(c, out, {}, {})  # noqa: E731
    assert run({"type": "count", "field": "tasks", "equals": 2})["reason"] == "2 items"
    assert run({"type": "count", "field": "tasks", "min": 1, "max": 2})["passed"]
    bad = run({"type": "count", "field": "tasks", "max": 1})
    assert not bad["passed"] and bad["reason"] == "2 items, expected max 1"
    assert not run({"type": "count", "field": "reply", "min": 1})["passed"]
    assert run({"type": "any", "field": "tasks", "where": {"kind": "leave"}})["reason"].startswith("item 2 of 2")
    assert run({"type": "any", "field": "tasks", "where": {"kind": "trip"}, "contains": {"event": "heidelberg"}})["passed"]
    assert run({"type": "any", "field": "tasks", "where": {"who.name": "elena"}})["passed"]
    miss = run({"type": "any", "field": "tasks", "where": {"kind": "trip"}, "contains": {"event": "Riga"}})
    assert not miss["passed"] and miss["reason"] == ('no item of 2 matches {"kind": "trip", "event": "~Riga"}; '
                                                     'closest is item 1: `event` is "E-Science-Tage, Heidelberg"')
    out["tasks"][1]["who"]["name"] = "Elena Popescu"  # `where` text = contains, case-insensitive
    assert run({"type": "any", "field": "tasks", "where": {"who.name": "elena", "kind": "LEAVE"}})["passed"]
    out["tasks"][0]["n"] = 12
    assert not run({"type": "any", "field": "tasks", "where": {"n": 1}})["passed"]  # numbers: equal
    assert "`n` is missing" in run({"type": "any", "field": "tasks", "where": {"n": 1, "kind": "leave"}})["reason"]
    assert cases.validate_case({"id": "a", "checks": [{"type": "count", "field": "tasks", "min": 1},
                                                      {"type": "any", "field": "tasks", "where": {"k": "v"}}]}) == []
    errors = cases.validate_case({"id": "a", "checks": [{"type": "count", "field": "t"},
                                                        {"type": "any", "field": "t", "where": "trip"}]})
    assert len(errors) == 2


def test_star_means_every_item_and_not_contains_on_missing():
    out = {"options": [{"trains": ["ICE 1"]}, {"trains": ["FlixBus N1"]}], "empty": [], "draft": ""}
    run = lambda c, exp={}: checks.run_check(c, out, exp, {})  # noqa: E731
    bad = run({"type": "not_contains", "field": "options.*.trains", "value": "FlixBus N1"})
    assert not bad["passed"] and bad["reason"].startswith("item 2 of 2")
    assert run({"type": "count", "field": "options.*.trains", "equals": 1})["reason"] == "all 2 items pass"
    assert run({"type": "contains", "field": "empty.*", "value": "x"})["passed"]  # no items: nothing fails
    exp = {"options": [{"trains": ["ICE 1"]}, {"trains": ["FlixBus N1"]}]}
    assert run({"type": "equals", "field": "options.*.trains"}, exp)["passed"]  # expected item by item
    assert run({"type": "not_contains", "field": "options.5.trains", "value": "Bus"})["passed"]  # missing
    assert run({"type": "not_contains", "field": "nothing.*.trains", "value": "Bus"})["passed"]
    assert run({"type": "not_contains", "field": "draft", "value": "Bus"})["passed"]  # empty
    assert not run({"type": "contains", "field": "nothing.*", "value": "x"})["passed"]


def test_judge_without_key_is_skipped(monkeypatch, capsys):
    monkeypatch.setattr(judge, "score", lambda *a: (_ for _ in ()).throw(RuntimeError("no OPENAI_API_KEY")))
    r = checks.run_check({"type": "judge", "field": "r", "rubric": "x"}, {"r": "hi"}, {}, {})
    assert r["passed"] is None and r["skipped"] and "OPENAI_API_KEY" in r["reason"]
    run = runner.run_eval("fake", "python", case_ids=["f01"])  # f01 has a judge check
    assert run["cases"][0]["passed"] and run["summary"]["skipped"] == {"judge": 1}
    assert "judge" not in run["summary"]["by_check"]
    assert cli.main("fake", "python", ["f01"], "") == 0
    assert "1 judge skipped" in capsys.readouterr().out


def test_schema_check():
    schema = json.loads((FIX / "usecases/fake/output.schema.json").read_text())
    assert checks.schema_check({"category": "a", "reply": ""}, schema)["passed"]
    bad = checks.schema_check({"category": "zzz", "reply": ""}, schema)
    assert not bad["passed"] and bad["field"] == "category"


def test_schema_errors_in_words():
    schema = {"type": "object", "required": ["kind", "tasks"], "additionalProperties": False,
              "properties": {"kind": {"enum": ["a", "b"]}, "n": {"type": "integer"},
                             "tasks": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                  "properties": {"x": {}}}}}}
    reason = lambda out: checks.schema_check(out, schema)["reason"]  # noqa: E731
    assert reason({"kind": "a", "tasks": [], "draft_to_note": 1}) == (
        "the agent added a field that is not in the schema: `draft_to_note`. Tell the agent to use only the "
        "fields of output_schema, or add the field to output.schema.json")
    assert reason({"kind": "a"}) == "the field `tasks` is missing"
    assert reason({"kind": "c", "tasks": []}).startswith('`kind` must be one of ["a", "b"]')
    assert reason({"kind": "a", "tasks": [], "n": "1"}).startswith("`n` must be of type integer")
    assert "`tasks.0.y`" in reason({"kind": "a", "tasks": [{"y": 1}]})


def test_read_claude_stream():
    lines = [{"type": "system", "subtype": "init"},
             {"type": "assistant", "message": {"content": [
                 {"type": "tool_use", "id": "t0", "name": "ToolSearch", "input": {"query": "x"}},
                 {"type": "tool_use", "id": "t1", "name": "mcp__kit__rules_get", "input": {"name": "routing"}}]}},
             {"type": "user", "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "# Routing"}]}]}},
             {"type": "result", "result": "{}", "num_turns": 2}]
    result, calls = engines.read_stream("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    assert result["num_turns"] == 2
    assert calls == [{"name": "rules_get", "input": {"name": "routing"}, "output": "# Routing"}]


def test_parse_json_text():
    assert engines.parse_json_text('x\n```json\n{"a": 1}\n```\nthen\n```json\n{"a": 2}\n```') == {"a": 2}
    assert engines.parse_json_text(' {"a": 3} ') == {"a": 3}


def test_read_python_tool_log():
    err = 'hello\ntool contacts_lookup {"query": "a -> b"} -> "x\\n\\"y\\""\ntool rules_get {} -> "r"\ntool broken'
    assert engines.read_tool_log(err) == [{"name": "contacts_lookup", "input": {"query": "a -> b"}, "output": 'x\n"y"'},
                                          {"name": "rules_get", "input": {}, "output": "r"}]


def test_judge_gets_tool_calls(monkeypatch):
    seen = {}
    monkeypatch.setattr(judge, "score", lambda value, rubric, inp, tools=None: seen.update(tools=tools) or (5, "ok"))
    run = runner.run_eval("fake", "python", case_ids=["f01"])
    assert seen["tools"] == [{"name": "rules_get", "input": {"name": "routing"}, "output": "# Routing\nVendor declined"}]
    assert run["cases"][0]["meta"]["tool_calls"] == seen["tools"]
    text = judge.tools_text([{"name": "t", "input": {}, "output": "z" * 5000}] * 9)
    assert len(text) == judge.TOOLS_LIMIT and "z" * judge.OUTPUT_LIMIT in text and "z" * 1501 not in text


# engines and runs


def test_python_run(tmp_path):
    events = []
    run = runner.run_eval("fake", "python", name="first", on_progress=events.append)
    by_id = {c["id"]: c for c in run["cases"]}
    assert by_id["f01"]["passed"], by_id["f01"]["checks"]
    assert not by_id["f02"]["passed"]
    assert "exit 1" in by_id["f03"]["error"] and "crashed on purpose" in by_id["f03"]["error"]
    assert not by_id["f04"]["passed"] and by_id["f04"]["checks"][0]["type"] == "schema"
    assert run["summary"]["cases"] == 4 and run["summary"]["passed"] == 1
    assert run["summary"]["by_check"]["judge"] == [1, 1]
    assert len([e for e in events if e["status"] == "done"]) == 4
    saved = runner.load_run(run["run_id"])
    assert saved["ended_at"] and saved["name"] == "first"
    assert runner.list_runs("fake")[0]["run_id"] == run["run_id"]
    assert "cases" not in runner.list_runs()[0]


def test_claude_run_with_fake_cli():
    run = runner.run_eval("fake", "claude", case_ids=["f01", "f03"])
    f01, f03 = run["cases"]
    assert f01["passed"] and f01["meta"]["turns"] == 2 and f01["meta"]["cost_usd"] == 0.01
    assert "Not logged in" in f03["error"]


def test_n8n_run(n8n):
    run = runner.run_eval("fake", "n8n", case_ids=["f01", "f02"])
    assert run["cases"][0]["output"]["tags"] == ["z"]
    assert FakeN8n.last["output_schema"]["type"] == "object"  # every engine gets the result shape
    assert run["cases"][0]["checks"][4]["type"] == "in" and not run["cases"][0]["checks"][4]["passed"]


def test_n8n_errors_say_what_to_do(n8n, monkeypatch):
    out, err, _ = engines.call_n8n("other", {"case_id": "x", "input": {}})
    assert out is None and "404" in err and "make setup" in err
    monkeypatch.setenv("N8N_URL", "http://127.0.0.1:9")
    out, err, _ = engines.call_n8n("fake", {"case_id": "x", "input": {}})
    assert "cannot reach n8n" in err


def test_n8n_that_sleeps_fails_in_time(n8n, monkeypatch):
    monkeypatch.setitem(engines.TIMEOUTS, "n8n", 1)
    t0 = time.time()
    res = runner.run_case("fake", "n8n", {"id": "s", "input": {"mode": "sleep"}}, {}, "r", "r")
    assert time.time() - t0 < 2.5 and "did not answer in 1 s" in res["error"] and res["seconds"] >= 1


def test_hard_deadline_when_the_engine_hangs(monkeypatch):
    monkeypatch.setitem(engines.TIMEOUTS, "python", 1)
    monkeypatch.setattr(engines, "GRACE_S", 0.5)
    monkeypatch.setattr(engines, "call", lambda *a, **k: time.sleep(5))
    t0 = time.time()
    res = runner.run_case("fake", "python", {"id": "h", "input": {}}, {}, "r", "r")
    assert time.time() - t0 < 2.5 and "hard deadline" in res["error"] and not res["passed"]


def test_compare():
    def case(cid, passed, checks_passed):
        return {"id": cid, "title": cid, "passed": passed,
                "checks": [{"type": "equals", "field": "x", "passed": i < checks_passed, "reason": ""} for i in range(3)]}

    store.save_run({"run_id": "a", "cases": [case("c1", False, 1), case("c2", True, 3), case("c3", False, 1),
                                               case("c4", False, 0)]})
    store.save_run({"run_id": "b", "cases": [case("c1", True, 3), case("c2", False, 2), case("c3", False, 2),
                                               case("c5", True, 3)]})
    changes = {r["id"]: r["change"] for r in runner.compare("a", "b")["cases"]}
    assert changes == {"c1": "better", "c2": "worse", "c3": "better", "c4": "removed", "c5": "new"}


def test_unknown_case_id_stops_start():
    with pytest.raises(ValueError, match="unknown case"):
        runner.run_eval("fake", "python", case_ids=["nope"])


def test_crash_in_one_case_does_not_stop_others(monkeypatch):
    real = engines.call

    def flaky(uc, engine, inp, env=None):
        if inp["case_id"] == "f02":
            raise RuntimeError("boom")
        return real(uc, engine, inp, env)

    monkeypatch.setattr(engines, "call", flaky)
    run = runner.run_eval("fake", "claude", case_ids=["f01", "f02"])
    assert run["cases"][0]["passed"] and "boom" in run["cases"][1]["error"]


def test_holdout_cases_and_per_field_summary(monkeypatch):
    real = runner.load_cases

    def with_holdout(uc):
        cs = real(uc)
        cs[1] = {**cs[1], "holdout": True}
        return cs

    monkeypatch.setattr(runner, "load_cases", with_holdout)
    ids = lambda run: [c["id"] for c in run["cases"]]  # noqa: E731
    run = runner.run_eval("fake", "claude")
    assert "f02" not in ids(run)
    assert "f02" in ids(runner.run_eval("fake", "claude", holdout=True))
    assert ids(runner.run_eval("fake", "claude", case_ids=["f02"])) == ["f02"]  # named: it runs
    assert run["summary"]["by_field"]["equals category"][1] >= 1 and run["cases"][0]["today"] == "2026-11-04"
    assert cases.validate_case({"id": "a", "holdout": "yes"}) == ["`holdout` must be true or false"]


def test_cli(capsys):
    assert cli.main("fake", "claude", ["f01", "f02"], "") == 0
    out = capsys.readouterr().out
    assert "✓ f01" in out and "✗ f02" in out and "got b, expected a" in out and "1/2 cases passed" in out
    assert cli.main("nope", "python") == 1


def test_engine_env_drops_empty_base_url(monkeypatch):
    """`OPENAI_BASE_URL=` in .env must not reach the SDK as "" (it then calls no host)."""
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert "OPENAI_BASE_URL" not in engines.engine_env()
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gw:4000/v1/")
    assert engines.engine_env()["OPENAI_BASE_URL"] == "http://gw:4000/v1"
