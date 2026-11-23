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
`LANGFUSE=local|hosted|off`. Compose project name: default `maxit-kit` (`name:` in `docker-compose.yml`); `COMPOSE_PROJECT_NAME` in
`.env` or the shell changes it (for example a second copy of the kit, with other ports).

## 2. Use case folder

```text
usecases/<uc>/
  BRIEF.md              the business problem (the team rewrites it)
  data/                 files the tools and the cases read
  cases.jsonl           the eval cases (§3)
  output.schema.json    JSON Schema of the result (§4)
  n8n/workflow.json     n8n workflow `Kit: <uc> workflow` (nodes; no AI Agent node), webhook path = <uc>
  n8n/agent.json        example: the same job as one AI Agent node, `Kit: <uc> agent (example)`, path = <uc>-agent
  n8n/prompts/*.md      the prompts of both n8n files (§5, n8n sync)
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
- `holdout`: optional, `true` = a case kept aside. `run_eval` skips it unless `holdout=True` (`kit eval
  --holdout`, `make eval HOLDOUT=1`, the tick on the Run page) or its id is in `case_ids`.
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
| `any` | `result[field]` is a list of objects, and one item matches all of `where` and all of `contains`. Text values match as case-insensitive substring ("Elena" matches "Elena Popescu"), in `where` too; numbers and true/false in `where` must be equal. Keys can be dotted paths in the item. On a fail the reason names the fields of the closest item that do not match | `where` and/or `contains`, e.g. `{"where": {"kind": "trip"}, "contains": {"event": "Riga"}}` |
| `judge` | an OpenAI model (`KIT_JUDGE_MODEL`) scores `result[field]` 1–5 against `rubric`, given the case input and the engine's tool calls (`meta.tool_calls`, a few KB; facts from tool results are allowed); pass ≥ `min`. It asks for temperature 0 and retries without it if the model refuses, so a judge result can flip between runs | `rubric`, `min` (default 4) |

The schema check (§4) always runs first. A case passes when the schema check and all its checks pass.
A `judge` that cannot run (no key, API error, timeout) is **skipped**: `"passed": null, "skipped": true`, the
reason says why. It does not count: the case passes or fails on its other checks, and the run summary counts it
under `"skipped": {"judge": n}` (not in `by_check`).

## 4. Engine contract

Engine input (JSON): `{"case_id": "m06", "today": "2026-11-04", "input": {<case input with file texts added>},
"output_schema": {<the parsed output.schema.json>}}`.
Engine output: one JSON object that must validate against `output_schema`. The scaffolds read the shape from
`output_schema` (Claude `CLAUDE.md`, `python/agent.py`, and the n8n model node of the last AI step, Response Format → Schema, by the expression
`$('Case in').item.json.body.output_schema`), so a schema change needs no copy in the engines.

| Engine | How the runner calls it | Timeout |
|---|---|---|
| `n8n` | `POST {N8N_URL}/webhook/<uc>`, body = engine input; the response body is the result (workflow ends with Respond to Webhook). The scaffold `n8n/workflow.json` is a chain of nodes (code decides the path, the model fills in the steps): marketing Read the rules → Triage (AI) → Junk? (IF; yes: **Result: no reply**, code) → Look up the sender in the contact sheet, Find earlier emails from the sender (tools) → Decide and draft (AI) → Read result → Result out; travel Read the rules → Read message (AI) → Look up events on those days (tool) → Find tasks (AI) → Read result → Result out. An AI step has no Structured Output Parser (it fails on a valid answer without its `{"output": ...}` wrapper): its model node uses the Responses API with Response Format = JSON Schema, and a Code node after it (`Triage fields`, `Message fields`, `Read result`) parses the JSON text into `output` (also wrapped or in a ```json block). Tool nodes are HTTP Request nodes on `{KIT_TOOLS_URL}/call/<tool>` (§7), body "Using Fields Below" (one field per argument); the runner finds tool calls by their answer `{"tool", "input", "output"}`, not by node name. Every main node has a short canvas note. The files have no `pinData` (they define the pipeline, not one case): push and pull drop all pins (Debug in editor pins every node), pins are not part of the "same workflow" hash, and setup removes the pins from a workflow it updates. If n8n answers an error, the runner reads the failed execution and reports `n8n failed at <node>: <message>` with the execution link | 120 s |
| `n8n-agent` | example engine, not in the default `KIT_ENGINES` (so not in `make check` unless listed; the Run page offers it when `n8n` is used): as `n8n`, but `POST {N8N_URL}/webhook/<uc>-agent`, the workflow `n8n/agent.json` (one AI Agent node with the kit MCP tools) | 120 s |
| `claude` | in `usecases/<uc>/claude/`: `claude -p <prompt> --output-format stream-json --verbose --mcp-config .mcp.json --strict-mcp-config --permission-mode dontAsk --allowedTools mcp__kit` + `--setting-sources project` + `--settings {"claudeMdExcludes": [the CLAUDE.md files of the parent folders up to the repo root], "skillOverrides": {<each skill in the root .claude/skills/>: "off"}}` (Claude Code also loads parent CLAUDE.md files and skills: those are for the builder session at the repo root, not the agent) + `--json-schema <output_schema without "$schema">`; prompt = "Process this case. Answer with only the result JSON.\n\n" + engine input. The result is `structured_output` of the last `result` event (Claude Code checks it against the schema); without it, the JSON in the `result` text (a fenced ```json block, or the whole text) | 300 s |
| `python` | in `usecases/<uc>/python/`: `uv run --project <repo root> python agent.py`, engine input on stdin, result JSON on stdout (stderr = logs). The scaffold uses the OpenAI Responses API (`/v1/responses`; new models allow function tools only there), also through `OPENAI_BASE_URL` | 300 s |

Timeouts are wall-clock time. Each case has a hard deadline of engine timeout + 10 s: then the runner stops
waiting and the case fails with "hard deadline". A `judge` check has its own limit (120 s). The n8n scaffolds set
`settings.executionTimeout` to 110 s (under the runner timeout). On macOS the runner keeps the computer awake
during an eval (`caffeinate -i`): a sleeping computer stops every engine.

Env passed to engines: `OPENAI_API_KEY`, `KIT_MODEL`, `KIT_TOOLS_URL`, and for Claude the OTel/`TRACEPARENT`
variables (§6). Claude uses the participant's own login.

## 5. Runner API (Python, `kit/runner/`) and run file

```python
from kit.runner import run_eval, list_runs, load_run, compare
run = run_eval(usecase, engine, case_ids=None, name=None, on_progress=None, holdout=False)  # blocking
list_runs(usecase=None) -> list[dict]   # newest first; summary fields and the sorted `case_ids`
load_run(run_id) -> dict
compare(run_id_a, run_id_b) -> dict     # per case: better / worse / same; agent_changed
n8n_warning(usecase, engine="n8n") -> str | None  # a warning if that n8n workflow (n8n or n8n-agent) is
                                        # not in sync with the files, or has edits that are not published
```

**n8n sync (files ↔ n8n).** n8n keeps its own copy of each workflow; the files are the source for git and for
Claude Code. A prompt field in `n8n/*.json` (chainLlm: the first chat message and `text`; agent:
`options.systemMessage` and `text`) holds `={{PROMPT:<name>}}`; the text is in `n8n/prompts/<name>.md` (`<name>` =
node name in snake case; `.input` = the `text` field, the user message; `agent.json` prompts start with
`agent`; the file ends with one newline that is not part of the prompt). `kit/n8n_files.py` fills and splits them.
- `make n8n-push [UC=] [FORCE=1]` (and `make setup`): files → n8n. Fills `{{PROMPT:…}}`, `{{KIT_MODEL}}`,
  `{{KIT_TOOLS_URL}}`, `{{OPENAI_CREDENTIAL_ID}}`, updates by name, publishes. Unpublished editor edits are
  discarded (old copy to `runs/n8n-backup/`).
- `make n8n-pull [UC=] [FORCE=1]` (`make n8n-export` = the same): n8n → files, the **published** version
  (`activeVersion`), placeholders and prompt files back, keys in the order of the old file: no change = the same
  bytes. It warns about prompt files that no node uses any more.
- **Conflict guard.** `runs/n8n-imported.json` keeps, per workflow name, `workflow_hash` of the last sync (push,
  pull, setup). The state is `same`, `n8n` (only n8n changed), `files` (only the files changed, e.g. a prompt
  edit or `git pull`), `both`, or `unknown` (they differ and there is no record). Push without `FORCE` keeps
  n8n in `n8n`/`both`/`unknown`; pull without `FORCE` stops in `files`/`both`/`unknown`. Each says which side
  changed and what to run; `FORCE=1` copies the overwritten side to `runs/n8n-backup/` first.
- **Before each n8n / n8n-agent eval** (`run_eval`: CLI and UI): `n8n` → pull, the run file gets
  `n8n_sync` ("n8n changes saved to … (not committed)"; the results page shows it); `files`, `both`, `unknown` →
  the run does not start (CaseError with the advice). The schema must be OpenAI-strict (every property in
  `required`, `additionalProperties: false` in each object), because the model nodes send it as strict
  json_schema: else the run does not start ("n8n needs a strict output.schema.json: …").
- `n8n_warning` (shown by `make eval`, `make check`, the UI Run page; kept in the run file as `warning`): the
  sync advice, and "has edits that are not published: Publish in n8n, or run make n8n-push to discard them".

`make setup` first tests the key with one `GET {base}/models` (OpenAI or `OPENAI_BASE_URL`). Without a key it
still imports the workflows, inactive and without the credential; the next run with a key activates them.

**Agent version.** Each run file has `agent_version`: `{"hash", "git"}`. n8n: `workflow_hash` of the workflow
file with its prompts (after the sync, so what ran); claude, python: a hash of all files of the engine folder.
`git` = `HEAD` (short) if that folder has no changes that are not committed, else null. `compare` sets
`agent_changed` when both runs of one engine have different hashes; the results page shows "workflow changed"
(n8n) or "agent changed"; `kit compare A B` (`make compare A= B=`) prints it.

`make check` names its runs `check`: the UI hides them in the Runs list (link "Show checks") and in Compare.
Compare defaults to the last older run of the same engine with the same case ids (else the last one that ran
all of these cases, else the last one of that engine), lists that engine first, and marks runs with other
case ids ("other cases").

`on_progress(event: dict)` is called with `{"case_id", "status": "running"|"done", "passed"?, "error"?}`. Cases run up to
4 in parallel (claude 2).

Run file `runs/<run_id>.json`, `run_id` = `<YYYYmmdd-HHMMSS>-<uc>-<engine>`:

```json
{"run_id": "...", "name": "...", "usecase": "marketing", "engine": "n8n", "started_at": "...", "ended_at": "...",
 "experiment_url": "http://localhost:5703/project/<id>/experiments/results?baseline=<id>",
 "summary": {"cases": 5, "passed": 4, "by_check": {"equals": [4, 5], "judge": [3, 3]},
             "by_field": {"schema": [5, 5], "equals category": [4, 5], "judge reply": [3, 3]}, "skipped": {}},
 "cases": [{"id": "m06", "title": "...", "passed": false, "seconds": 3.2, "error": null,
            "input": {...}, "expected": {...}, "output": {...},
            "checks": [{"type": "equals", "field": "category", "passed": false, "reason": "got unverified_outreach, expected service_offer",
                        "def": {"type": "equals", "field": "category"}}],
            "trace_url": "http://localhost:5703/...", "n8n_execution_url": "http://localhost:5702/workflow/<id>/executions/<n>"}]}
```

Each case check result keeps `def`: the check from `cases.jsonl` as it was at run time (`value`, `values`,
`rubric`, `min`, `where`, `contains`, …; the schema check has none). The results page shows the check targets
from `def`, so an old run keeps its own targets after a case edit. For older run files without `def`, the page
takes them from today's `cases.jsonl` (only if the `case_hash` is the same) and says "(from today's case)".

Each case result also has `today` (the agent's date) and, for claude and python, `meta.stderr` (the last lines
of the engine's stderr; the results page shows it as "Engine log"). `meta.tool_calls` (`[{"name", "input",
"output"}]`): claude from its stream-json, python from the `tool <name> <args JSON> -> <result JSON string>` lines
that `agent.py` writes to stderr; n8n from the execution data (found by the `x-kit-call` header, needs
`N8N_API_KEY`): every run of a node whose output is a plain HTTP tool answer `{"tool", "input", "output"}` (§7;
the workflows), and every run of a tool node (output `ai_tool`, the agent example), name without the `<node>__`
prefix. `kit run-case <uc> <engine> <case_id>` runs
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
**n8n** (engines `n8n` and `n8n-agent`): Langfuse has no n8n tracing, so the runner reads the execution data it
already fetches (`engines.n8n_nodes`) and writes **one observation per executed node run** under the case trace:
name = node name (a tool sub-node run: `<node>: <tool>`), start and end time from the execution, input and
output (cut to 4000 characters; a main node's input = the output of the node before it), metadata
`n8n_node_type` (and `branch` for an IF/Switch), level `ERROR` with the message if the node failed. Types: model
sub-nodes = GENERATION with model and token usage, LLM chain nodes = CHAIN, the AI Agent node = AGENT, tool
nodes (the plain HTTP tool calls and MCP tool runs) = TOOL, other nodes = SPAN. Sub-nodes (model, tool, output
parser) nest under their AI node; the rest is flat under the case root. The `tool:<name>` spans are written for
n8n too. Not found (no `N8N_API_KEY`) or any error: no node spans, the case goes on. The run file does not get
the nodes.

**Dataset and experiment** (`run_eval`: UI Run and `make eval`; not `make check` runs, named `check`, and not
`run-case`). Before the cases run, the runner syncs **all** cases of `cases.jsonl` (holdout too) to the Langfuse
dataset `<uc>`: one item per case, item id `<uc>-<case_id>`, input = case `input`, expected output = case
`expected`, metadata = `title`, `today`, `checks`, `holdout`. Only changed items are written. Items of removed
cases are set to `ARCHIVED` (not deleted); a case that comes back is `ACTIVE` again. `cases.jsonl` stays the
source of truth.
The eval run is one **dataset run** (experiment) on that dataset: id = `Langfuse.create_trace_id(seed=run_id)`,
name = `<run name> (<run_id>)`, or only `<run_id>` when the run has no name. Run metadata: `engine`, `model`
(`KIT_MODEL`), `judge_model`, `git` (short commit). Each case trace is the run item of its case (the experiment
attributes that the SDK's `run_experiment` sets, on the case root span). Scores stay on the traces, so the
Langfuse compare view shows them per item. The run file gets `experiment_url`
(`{LANGFUSE_HOST}/project/<id>/experiments/results?baseline=<experiment id>`): the results page links it ("Open
this experiment in Langfuse"), `make eval` prints it. Langfuse down or slow: the sync waits at most 20 s, then
the run goes on without an experiment (`experiment_url: null`, a WARN line on stderr).

## 7. Tools (MCP server `kit/tools.py`, server name `kit`)

FastMCP, streamable HTTP on `:5701/mcp`. Each tool is a plain function with a docstring; a team adds a tool by
adding a function. `kit/tools.py` exposes `serve()` (blocking) and `start_background()` (daemon thread, used by
`kit serve`). Tools read only `usecases/*/data/`. No tool sends anything.

The same tools also answer **plain HTTP** (for n8n nodes without an agent): `POST :5701/mcp/call/<tool>`, body =
the arguments as a JSON object; answer `{"tool": <name>, "input": <the body>, "output": <the tool result>}`.
The answer repeats the input, so the runner finds each call in the n8n execution data (§5). Unknown tool: 404;
body not an object: 400; a wrong argument: 200 with `output = {"error", "hint"}`. A new tool function works here too.

| Tool | Returns |
|---|---|
| `contacts_lookup(query)` | matching contacts from `usecases/marketing/data/contacts.csv` |
| `inbox_history(sender, before=None)` | earlier emails of that sender, newest first (file, from, date, subject, first 500 chars). `before` (YYYY-MM-DD): only emails sent before that day. The scaffolds pass `before=<today>` of the case; without it you also get emails sent after the case's "today" |
| `events_list(date_from=None, date_to=None)` | events from `usecases/travel/data/events.csv` that overlap the span (both optional, YYYY-MM-DD); events without a date are in `undated` |
| `trains_search(from_place, to_place, date, arrive_by=None, depart_after=None, modes="rail")` | up to 5 connections on that day, no prices (MOTIS plan API: `TRAINS_URL`, fallback `TRAINS_FALLBACK_URL`). `arrive_by`: the last ones that arrive before the time, latest first; `depart_after`: from that time on (default 06:00). `modes`: `rail` (trains and S-Bahn, no buses) or `all` |
| `rules_get(usecase, name="")` | the text of `usecases/<uc>/data/<name>.md`; an empty or unknown name gives the list of names. Several names with commas (`"routing,reply_guidelines"`): one text, each part starts with `=== <name>.md ===` |
