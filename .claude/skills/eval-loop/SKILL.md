---
name: eval-loop
description: Measure and improve a kit agent on any engine (n8n, claude, python). Use to run an eval or one case, read a run file (runs/*.json), compare two runs, fix a failing case, add a case for a new failure, or change the output schema.
---

# The eval loop

One loop, the same on every engine: **run → read → change ONE thing → run again → compare**. A run file is the
evidence; the score counts only if the cases are honest. Method and check guide: `docs/the-loop.md`.

## Where each agent is

| Engine | What you change |
|---|---|
| `n8n` | `usecases/<uc>/n8n/prompts/*.md` and `n8n/workflow.json`, then `make n8n-push UC=<uc>` (skill `n8n-workflow`) |
| `claude` | `usecases/<uc>/claude/CLAUDE.md` and its skills in `usecases/<uc>/claude/.claude/skills/` (the agent's own, not this session's) |
| `python` | `usecases/<uc>/python/agent.py`: the `PROMPT` text, the tools list, the loop |

Per engine: `usecases/<uc>/<engine>/rules/*.md` (the owner's rules; the agent reads all of them through `rules_get`
on every case; a rule change is an agent change in Compare). Shared by all engines: `data/` (the inputs),
`output.schema.json` (the result shape; every engine gets it with each case), `cases.jsonl`.

## 1. Run

- All cases: `make eval UC=<uc> ENGINE=<engine> NAME=<what-you-changed>`. Holdout cases are skipped.
  Some cases: `CASES=m12,m14`. It ends with `Run file: runs/<run_id>.json`.
- One case with all detail (input, output, tool calls, engine log, checks; no run file):
  `make run-case UC=<uc> ENGINE=<engine> CASE=<id>`.
- `make serve` must run (the tools server). An eval line `Cannot start the run: …` says what to fix first
  (for n8n: a sync STOP or a schema that is not strict).

## 2. Read the run file

`runs/<run_id>.json`: `summary` (`passed`/`cases`, `by_field`: the weak field), then per case:
`passed`, `error` (the engine gave no answer: fix the setup or the engine, not the prompt), `checks[]`
(`type`, `field`, `passed`, `reason`, `def` = the check as it was), `output` next to `expected`,
`meta.tool_calls` (`name`, `input`, `output`: what the agent looked up and got), `meta.stderr` (claude, python),
`n8n_execution_url`, `trace_url` (Langfuse). Read the failing `reason`, then the tool calls, then the output.

For each red case decide: **is the agent wrong, or the case?** A wrong case (bad expected value, a check that is
too strict) is a question for the person who owns the use case; write down the question, do not bend the case.

## 3. Change one thing

One change per eval: one prompt line, one rule, one tool, the model, or one schema field. Write the rule so it
holds for the next email too, not for this one case (no sender names, no case ids in prompts).

**A new output field** is one change in two places: the field in `output.schema.json` (in `properties` and in
`required`; objects keep `additionalProperties: false`, an empty value is `""` or `[]`; n8n refuses a schema
that is not strict) plus one line in the prompt of the engine.

## 4. Run again and compare

`make eval … NAME=<new-name>`, then `make compare A=<run_id before> B=<run_id after>` (the start of an id
is enough: `A=20261123-141451`): ▲ better, ▼ worse per
case, `(case changed)` when the case itself changed, `workflow changed` / `agent files changed` when the agent
files differ. Keep the change if the target case is better and nothing is worse; else undo it.
A case that flips with no change is noise: run twice before you trust a small change (judge checks too).

## 5. A failure becomes a case

Write the case **before** the fix: one JSON object per line in `usecases/<uc>/cases.jsonl`, a new unique `id`,
a `title` that names the mistake, `today`, `input` (files: `"email_file": "data/emails/…"`), `expected`, and 1–2
`checks` for this mistake. Copy a near case as the start. Check types and fields: `docs/contracts.md` §3
(`equals`, `in`, `contains`, `not_contains`, `count`, `any`, `judge` with a `rubric`; `*` for every list item).
Run only the new case (`CASES=<id>`): it must fail on the old agent.

**Holdout:** `"holdout": true` keeps a case aside; `make eval … HOLDOUT=1` runs them at the end, to see that
the agent did not only learn the visible cases. Tune on the others only.

## Done

A loop round is done when the run file of the change exists, the compare is read, and the change is kept
(committed by the person) or undone. Report: run ids, score before → after, which cases flipped, and why.
