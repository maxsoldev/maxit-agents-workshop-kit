# The loop: eval-driven development in one page

You do not know if a change makes the agent better until you measure it. So: **cases first, then build,
then measure every change.**

## The steps

1. **Write the cases.** Each case is one input, the expected answer, and the checks (`cases.jsonl`).
   Start with 5: 2 easy ones, 2 tricky ones, 1 that must never go wrong (a safety case). If you changed
   `output.schema.json`, the starter cases check old fields: change them to the new fields, or delete them.
2. **Run the eval** on the scaffold. It fails. Good: now you see the gap. **A high first score means your
   cases are too easy: add harder ones** (follow-ups, deadlines, facts the agent must look up, safety).
3. **Read the failures.** Open each failed case: the output, the reason of each check, the trace.
   Ask: is the agent wrong, or is the case wrong?
4. **Change one thing.** The prompt, one rule, one tool, the model, the schema. Only one.
5. **Run the eval again.** Compare with the last run (Results → Compare). Better: keep it and commit.
   Worse: undo it. Same: think again. n8n: your change lives in n8n, not in git. Run `make n8n-export`
   (it writes `usecases/<uc>/n8n/workflow.json` and `agent.json` with the placeholders back), then commit them.
6. **Every new failure becomes a case.** When you see a mistake (in a trace, in a demo, in real use), write a
   case for it before you fix it. Then the fix stays fixed.

## In Langfuse

Langfuse has the same loop with other words. Your **cases** are a **dataset** (Datasets → `marketing`, one item
per case). Each eval **run** is an **experiment** (a dataset run) on that dataset. Each **check** is a **score**
(1 or 0) on the case trace, plus `passed`. To compare two runs: Experiments → tick both → **Compare** (or the
link **Open this experiment in Langfuse** on the results page). It shows the scores per case, side by side.
`cases.jsonl` stays the source: each eval writes the changed cases to the dataset.

## Rules that save time

- **One change at a time.** With two changes you do not know which one helped. A new output field is one
  change, also if it touches several files: the field in `output.schema.json`, plus one line about it in the
  prompt of each engine you use (`claude/CLAUDE.md`, the n8n system message, `python/agent.py`). The n8n
  **Result shape** node needs no edit: it reads the schema from the case.
- **Read the trace, not only the score.** The trace shows which tools the agent called, what it read and
  where it went wrong. Claude: Langfuse (the `tool:<name>` spans have the input and output; the results page
  lists the same tool calls, for all engines). n8n: Executions (the results page links to each one). Python: the `tool ...`
  lines it logs on stderr (the results page shows them under **Engine log**).
  To look at one case closely: `make run-case UC=marketing ENGINE=python CASE=m14` (or `uv run kit run-case
  marketing python m14`) prints the engine input, the output, the tool calls, the log and the checks.
- **Try a tool by hand** before you blame the agent: `uv run kit tool` lists the tools;
  `uv run kit tool trains_search "from_place=München Hbf" "to_place=Berlin Hbf" date=2026-12-08 arrive_by=10:00`
  prints what the agent gets.
- **Do not fit the cases.** A rule like "if the sender is Lara Venn, say unverified" makes the case pass and
  the agent worse. Write rules that hold for the next email too.
- **Run it twice** before you trust a small change. Models are not fully deterministic. This is true for the
  `judge` too (the kit asks for temperature 0, but many models do not allow it): a judge check can pass on one
  run and fail on the next with no change. Trust a judge result only when it is the same twice.
- **A flaky case is a finding.** Run the first eval twice. A case that passes once and fails once, with no
  change, often means the agent skips a step on some runs (for example it does not read `routing` every time).
  Look at the tool calls of both runs. Then make that step explicit in the prompt ("Step 1: call
  `rules_get` for `routing`"), and run twice again.
- **How to read a trace (Langfuse, for everyone):** open the case trace from the results page. Read the
  `tool:<name>` lines from top to bottom: each one is one thing the agent looked up (`tool:rules_get` with
  `"name": "priorities"` = "it read your priorities"), with what it got back. Skip the technical lines
  (`claude_code.llm_request` = one model call, `ToolSearch`, `blocked_on_user`): they are not your rules.
- **Keep 3 to 5 cases aside.** After many loops, your cases get easy: the agent is tuned to them. Mark a few
  new cases as **holdout** (tick in the case form, or `"holdout": true`). The eval skips them; run them at the
  end (`make eval ... HOLDOUT=1`, or the tick on the Run page) to see if the agent works on cases it never saw.
- **Read the score per field** (results page, "Per field", e.g. `equals action 8/15`): it shows which part of
  the result is weak.
- **Fix the case** when the case is wrong. Ask the owner what is right; do not change it to make it pass.

## Which check when

| Check | Use it for | Example |
|---|---|---|
| `equals` | one right value | `category` is `newsletter` |
| `in` | a few right values | `category` is `partnership` or `press` |
| `contains` | a word in a text, an item in a list | `flags` has `injection`; the reply names "28" |
| `not_contains` | things that must never appear (a missing or empty field passes) | no `@` in the reply (no leaked addresses) |
| any check with `*` | every item of a list must pass | `not_contains` `options.*.trains` "FlixBus": no option is a bus |
| `count` | how many items a list has | `tasks` has exactly 1 item (no duplicates): `{"equals": 1}` |
| `any` | "there is an item where ...", in any order | a task with `kind` `trip` whose `event` contains "Riga" |
| `judge` | quality you cannot check with a word: tone, "asks for details, no call" | rubric in one or two sentences |

The **schema** check always runs first: the result must match `output.schema.json`. A check on a field that is
not in the schema fails with "missing": add the field to the schema. The runner sends the schema to every engine
(`output_schema` in the engine input): the Claude agent, `agent.py` and the n8n **Result shape** node read it from
there. Say in the prompt what the new field means, if its name and description are not enough.

Prefer simple checks: they are fast, free and never wrong. Use `judge` for quality only, and write the rubric
like an instruction to a colleague: what must be in it, what must not.

**Tone and form → `judge`, not `contains`.** `contains` is a plain substring test. "The reply uses the Sie
form" is not the same as "the reply has the word Sie": a polite German reply can pass on one run and fail on
the next. Use `contains` only for a fact that must be there word for word (a date, a number, a name).
The same for `not_contains`: a word check can hit a correct text. `not_contains "Termin"` also fails on
"we will check our dates and send you a Terminvorschlag later", and `not_contains "call"` fails on "we do
not offer a call". For a must-not-say rule with a condition ("no meeting offer", "no promise before the head
approves"), use a `judge` with that rule as the rubric.
A judge that cannot run (no key, API error) is **skipped**: the case passes or fails on its other checks.

**What the judge sees:** the rubric, the case input, the field, and the agent's tool calls with their results
(all 3 engines; n8n through its execution data). So a fact the agent looked up (a contact note, a rule, an
event date) is not "invented". When a fact is not in a tool result, write the allowed facts into the
rubric ("The contact sheet says we declined NodeHost Pro in March 2026").

## How business people write cases (in the UI)

Open http://localhost:5700 → your use case → **Cases** → **Add case**.

1. **Title:** what the situation is, in a few words ("Unknown fund wants a call").
2. **Input:** pick an email (`data/emails/...`) or one chat message (`mattermost: ...`) and click **Add to
   input**, or paste a message into **Or paste a message**. The form writes the JSON for you and sets
   **Today** from the date of the email or message.
3. **Expected:** what a good colleague would answer, field by field (category, route, date, ...).
4. **Checks:** for each important point, one check. "Must never" points become `not_contains` or a `judge`.
   The field is a path into the result: `tasks.0.kind` = the kind of the first task (`tasks.1` = the second).
   "Some task is a trip to Riga" = an `any` check on `tasks`. The case list shows every check in words.

Then save. The case goes into `cases.jsonl`, so git shows the change. A developer never has to write the
expected answer: the person who does the job today knows it best.
