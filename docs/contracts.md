# Contracts (the fixed seams of the kit)

Every part of the kit builds against these. Change them only together with all parts.

## 1. Where things run

| What | Where | Port (127.0.0.1) |
|---|---|---|
| Kit UI (`kit serve`) | host, `uv run kit serve` | 5700 |
| Tools MCP server (started by `kit serve`, or `kit tools`) | host | 5701, path `/mcp` |
| n8n (compose profile `n8n`) | Docker | 5702 |
| Langfuse web (compose profile `langfuse`) | Docker | 5703 |

Settings come only from `kit/config.py` (`settings()`), which reads `.env` again on each call (so a new key
works without a restart of `make serve`; a variable set in the shell wins over `.env`). Modes: `N8N=local|hosted`,
`LANGFUSE=local|hosted|off`. Compose project name: `maxit-kit`.

## 2. Use case folder

```text
usecases/<uc>/
  BRIEF.md              the business problem (the team rewrites it)
  data/                 files the tools and the cases read
  cases.jsonl           the eval cases (§3)
  output.schema.json    JSON Schema of the result (§4)
  n8n/workflow.json     n8n workflow, webhook path = <uc>
  claude/               Claude Code agent folder: CLAUDE.md, .mcp.json, .claude/settings.json
  python/agent.py       Python agent: stdin → stdout
```

## 3. Case (one JSON object per line in `cases.jsonl`)

```json
{"id": "m06", "title": "Service offer (NodeHost Pro)", "today": "2026-11-04",
 "input": {"email_file": "data/emails/06.eml"},
 "expected": {"category": "service_offer"},
 "checks": [{"type": "equals", "field": "category"},
            {"type": "not_contains", "field": "reply", "value": "@"},
            {"type": "judge", "field": "reply", "rubric": "Polite no; does not promise a call."}]}
```

- `id` unique in the file; `title` short; `today` ISO date (the agent's "today"), optional.
- `input`: free JSON. Every key that ends in `_file` is a path relative to the use case folder; the runner adds
  the file text under the same key without `_file` (for example `email_file` → `email`). Engines never read files.
  An `.eml` file is decoded: `email` = readable text ("From: …", "To: …", "Date: …", "Subject: …", a blank line,
  then the plain body; an HTML-only body becomes text), and `email_raw` = the raw file. More files of one kind:
  `email_2_file` → `email_2`, and so on.
- `expected`: free JSON, used by checks.
- `checks`: list. `field` is a dotted path into the result (`reply`, `flags`, `options.0.price`; `0` = the first
  item). `*` = every item: `options.*.trains` runs the check on `trains` of each option, and all must pass (an
  empty list passes); for `equals` without `value`, item i compares with `expected` at `options.i.trains`.

| type | passes when | extra keys |
|---|---|---|
| `equals` | `result[field] == expected[field]` (or `value` if given) | `value` (optional) |
| `in` | `result[field]` is one of `values` | `values` |
| `contains` | `value` is in `result[field]` (substring for text, member for a list); case-insensitive for text | `value` |
| `not_contains` | the opposite of `contains`; also passes when the field is missing or empty | `value` |
| `count` | `result[field]` is a list, and its length is `equals` / at least `min` / at most `max` | one or more of `equals`, `min`, `max` (whole numbers) |
| `any` | `result[field]` is a list of objects, and one item matches all of `where` (equal as text, case-insensitive) and all of `contains` (substring, case-insensitive); keys can be dotted paths in the item | `where` and/or `contains`, e.g. `{"where": {"kind": "trip"}, "contains": {"event": "Riga"}}` |
| `judge` | an OpenAI model (`KIT_JUDGE_MODEL`) scores `result[field]` 1–5 against `rubric`, given the case input; pass ≥ `min` | `rubric`, `min` (default 4) |

The schema check (§4) always runs first. A case passes when the schema check and all its checks pass.
A `judge` that cannot run (no key, API error, timeout) is **skipped**: `"passed": null, "skipped": true`, the
reason says why. It does not count: the case passes or fails on its other checks, and the run summary counts it
under `"skipped": {"judge": n}` (not in `by_check`).

## 4. Engine contract

Engine input (JSON): `{"case_id": "m06", "today": "2026-11-04", "input": {<case input with file texts added>},
"output_schema": {<the parsed output.schema.json>}}`.
Engine output: one JSON object that must validate against `output_schema`. The scaffolds read the shape from
`output_schema` (Claude `CLAUDE.md`, `python/agent.py`, and the n8n **Result shape** node by the expression
`$('Case in').item.json.body.output_schema`), so a schema change needs no copy in the engines.

| Engine | How the runner calls it | Timeout |
|---|---|---|
| `n8n` | `POST {N8N_URL}/webhook/<uc>`, body = engine input; the response body is the result (workflow ends with Respond to Webhook) | 120 s |
| `claude` | in `usecases/<uc>/claude/`: `claude -p <prompt> --output-format stream-json --verbose --mcp-config .mcp.json --strict-mcp-config --permission-mode dontAsk --allowedTools mcp__kit` + `--setting-sources project`; prompt = "Process this case. Answer with only the result JSON.\n\n" + engine input. The result is the JSON in the `result` text of the last `result` event (a fenced ```json block, or the whole text) | 300 s |
| `python` | in `usecases/<uc>/python/`: `uv run --project <repo root> python agent.py`, engine input on stdin, result JSON on stdout (stderr = logs) | 300 s |

Timeouts are wall-clock time. Each case has a hard deadline of engine timeout + 10 s: then the runner stops
waiting and the case fails with "hard deadline". A `judge` check has its own limit (120 s). The n8n scaffolds set
`settings.executionTimeout` to 110 s (under the runner timeout). On macOS the runner keeps the computer awake
during an eval (`caffeinate -i`): a sleeping computer stops every engine.

Env passed to engines: `OPENAI_API_KEY`, `KIT_MODEL`, `KIT_TOOLS_URL`, and for Claude the OTel/`TRACEPARENT`
variables (§6). Claude uses the participant's own login.

## 5. Runner API (Python, `kit/runner/`) and run file

```python
from kit.runner import run_eval, list_runs, load_run, compare
run = run_eval(usecase, engine, case_ids=None, name=None, on_progress=None)  # blocking; returns the run dict
list_runs(usecase=None) -> list[dict]   # newest first; summary fields and the sorted `case_ids`
load_run(run_id) -> dict
compare(run_id_a, run_id_b) -> dict     # per case: better / worse / same
```

`make check` names its runs `check`: the UI hides them in the Runs list (link "Show checks") and in Compare.
Compare defaults to the previous run of the same engine, lists that engine first, and marks runs with other
case ids ("other cases").

`on_progress(event: dict)` is called with `{"case_id", "status": "running"|"done", "passed"?}`. Cases run up to
4 in parallel (claude 2).

Run file `runs/<run_id>.json`, `run_id` = `<YYYYmmdd-HHMMSS>-<uc>-<engine>`:

```json
{"run_id": "...", "name": "...", "usecase": "marketing", "engine": "n8n", "started_at": "...", "ended_at": "...",
 "summary": {"cases": 5, "passed": 4, "by_check": {"equals": [4, 5], "judge": [3, 3]}, "skipped": {}},
 "cases": [{"id": "m06", "title": "...", "passed": false, "seconds": 3.2, "error": null,
            "input": {...}, "expected": {...}, "output": {...},
            "checks": [{"type": "equals", "field": "category", "passed": false, "reason": "got unverified_outreach, expected service_offer"}],
            "trace_url": "http://localhost:5703/...", "n8n_execution_url": "http://localhost:5702/workflow/<id>/executions/<n>"}]}
```

Each case result also has `today` (the agent's date) and, for claude and python, `meta.stderr` (the last lines
of the engine's stderr; the results page shows it as "Engine log"). `kit run-case <uc> <engine> <case_id>` runs
one case without a run file and prints the engine input, output, tool calls, stderr and checks.

`case_hash` (each case result): a short hash of the case's `today`, `input`, `expected` and `checks`. Compare
marks a case "case changed" when the hash differs between the two runs (and "new case" when it was not in the
other run); the Runs list shows "13 cases (2 new, 1 changed)" against the run before. `list_runs` rows have
`case_hashes` (`{id: hash}`).

`n8n_execution_url` (n8n only, needs `N8N_API_KEY`): the runner sends an `x-kit-call` header with each POST and
finds the execution with that header; if it cannot, the link goes to the workflow's Executions list.

## 6. Tracing

If `settings().langfuse_on`: one Langfuse trace per case (name `<uc>/<case_id>`, tags `[uc, engine, run name]`,
session id = `run_id`), input = engine input, output = result, one score per check (1/0) and `passed`.
Claude gets `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`, `OTEL_TRACES_EXPORTER=otlp`,
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT={LANGFUSE_HOST}/api/public/otel/v1/traces`, basic auth header, and
`TRACEPARENT=00-<trace_id>-<span_id>-01` of the case trace, so its spans nest under it. If off: no traces, no links.
Claude also gets `OTEL_LOG_TOOL_DETAILS=1`, `OTEL_LOG_TOOL_CONTENT=1`, `OTEL_LOG_USER_PROMPTS=1`,
`OTEL_LOG_ASSISTANT_RESPONSES=1`. Claude Code 2.1 still sends no tool input in its spans (the tool output is only
a span event, which Langfuse does not show). So the runner reads the tool calls from Claude's stream-json and
writes one TOOL span per call, `tool:<name>`, with input and output, under the case trace. The run file has
them too (`meta.tool_calls`, output cut to 4000 characters); the results page lists them.

## 7. Tools (MCP server `kit/tools.py`, server name `kit`)

FastMCP, streamable HTTP on `:5701/mcp`. Each tool is a plain function with a docstring; a team adds a tool by
adding a function. `kit/tools.py` exposes `serve()` (blocking) and `start_background()` (daemon thread, used by
`kit serve`). Tools read only `usecases/*/data/`. No tool sends anything.

| Tool | Returns |
|---|---|
| `contacts_lookup(query)` | matching contacts from `usecases/marketing/data/contacts.csv` |
| `inbox_history(sender)` | earlier emails of that sender (from, date, subject, first 500 chars) |
| `events_list(date_from, date_to)` | events from `usecases/travel/data/events.csv` |
| `trains_search(from_place, to_place, date, arrive_by=None, depart_after=None, modes="rail")` | up to 5 connections on that day, no prices (MOTIS plan API: `TRAINS_URL`, fallback `TRAINS_FALLBACK_URL`). `arrive_by`: the last ones that arrive before the time, latest first; `depart_after`: from that time on (default 06:00). `modes`: `rail` (trains and S-Bahn, no buses) or `all` |
| `rules_get(usecase, name)` | the text of `usecases/<uc>/data/<name>.md` |
