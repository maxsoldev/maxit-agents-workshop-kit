# The loop: eval-driven development in one page

You do not know if a change makes the agent better until you measure it. So: **cases first, then build,
then measure every change.**

## The steps

1. **Write the cases** (`cases.jsonl`): an input, the expected answer, the checks. Start with 5: 2 easy, 2
   tricky, 1 that must never go wrong (safety). Changed `output.schema.json`? Change or delete the starter cases.
2. **Run the eval** on the scaffold. It fails: good. **A high first score means your cases are too easy: add
   harder ones** (follow-ups, deadlines, facts to look up, safety).
3. **Read the failures:** the output, the reason of each check, the trace. Is the agent wrong, or the case?
4. **Change one thing:** the prompt, one rule, one tool, the model, the schema. Only one.
5. **Run the eval again** and compare (results page → **Compare with**). Better: keep it and commit. Worse: undo it.
   n8n: the eval saves your n8n graph edits to `n8n/workflow.json`: commit them.
6. **Every new failure becomes a case.** Write the case before you fix it. Then the fix stays fixed.

## Rules that save time

- **One change at a time.** A new output field is one change: the field in `output.schema.json` plus one line
  in the prompt of each engine you use (`claude/CLAUDE.md`, `n8n/<step>.md`, `python/prompt.md`).
- **Read the trace, not only the score.** The results page lists the tool calls for all engines. More detail:
  Langfuse (link on the results page), for n8n also **n8n execution ↗**, for Python the **Engine log**.
- **Look at one case closely:** `make run-case UC=marketing ENGINE=python CASE=m15` prints the input, output,
  tool calls, log and checks.
- **Try a tool by hand** before you blame the agent: `uv run kit tool` lists the tools;
  `uv run kit tool trains_search "from_place=München Hbf" "to_place=Berlin Hbf" date=2026-12-08 arrive_by=10:00`
  prints what the agent gets.
- **Do not fit the cases.** "If the sender is Lara Venn, say unverified" makes the case pass and the agent
  worse. Write rules that hold for the next email too.
- **Run it twice** before you trust a small change, also for `judge` checks. Trust a result only when it is the
  same twice.
- **A flaky case is a finding.** It often means the agent skips a step on some runs. Compare the tool calls of
  both runs, make that step explicit in the prompt ("Step 2: call `inbox_history` for the sender"), run twice again.
- **Keep 3 to 5 cases aside** as **holdout** (tick in the case form). The eval skips them; run them at the end
  (the tick on the Evals page, or `make eval ... HOLDOUT=1`).
- **Read the score per field** (results page, "Per field"): it shows the weak part of the result.
- **Fix the case** when the case is wrong. Ask the owner what is right; do not change it to make it pass.

## How to read a trace (Langfuse)

Open the case trace from the results page. Read the `tool:<name>` lines from top to bottom: each one is one
thing the agent looked up, with what it got back (`tool:inbox_history` with `"sender": "Wollner"` = "it read her
earlier emails"). Skip the technical lines (`claude_code.llm_request`, `ToolSearch`, `blocked_on_user`).
An n8n trace has one line per node, in run order; open the model line under an AI node to see its prompt and
answer.

In Langfuse your **cases** are a **dataset**, each eval run is an **experiment**, each check is a **score**.
To compare runs: Experiments → tick both → **Compare** (or **Open this experiment in Langfuse** on the results
page). For time and cost per case: **Columns** → tick **Cost ($)** and **Latency (ms)**. The kit results page
shows them too (Time, Cost, the totals, and Δ time / Δ cost against the compare run). Cheaper and faster
counts only if the score stays the same.

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

- The **schema** check runs first. A check on a field that is not in the schema fails with "missing": add the
  field to the schema.
- Prefer simple checks: fast, free, never wrong. Use `judge` for quality; write the rubric like an instruction
  to a colleague.
- **Tone, form and "must not say" rules → `judge`.** `contains` is a plain substring test: use it only for a
  fact that must be there word for word (a date, a number, a name). `not_contains "call"` also fails on "we do
  not offer a call".
- **The judge sees** the rubric, the case input, the field and the agent's tool calls with their results. A
  fact the agent looked up is not "invented". A fact that is in no tool result: write it into the rubric.
- A judge that cannot run (no key, API error) is **skipped**: the case passes or fails on its other checks.

## How business people change the rules (in the UI)

Open **Rules** and pick your engine: its business rules file (`CLAUDE.md`, `prompt.md`, `n8n/<step>.md`):
the steps and the owner's lists (categories, flags, kinds), written as lists, not tables. Change a rule,
**Save** (Ctrl+S), then run the eval on that engine and compare (the results page shows "agent changed"). The categories,
flags and kinds live only in this file: add one there, with no change to the code or the schema. The page and
the file stay in sync: an edit on disk (by a developer or Claude Code) shows in the page, and the page never
saves over an edit it has not seen.

**Try** runs the agent on one email or message, as a person uses it: no checks, no run file. A good or a
bad answer? **Make a case from this**.

## How business people write cases (in the UI)

Open http://localhost:5700 → your use case → **Cases** → **Add case**.

1. **Title:** the situation in a few words ("Unknown fund wants a call").
2. **Input:** pick an email or a chat message and click **Add to input**, or paste a message. **Today** is set
   from its date.
3. **Expected:** what a good colleague would answer, field by field.
4. **Checks:** one check for each important point. "Must never" points become `not_contains` or a `judge`.
   The field is a path into the result: `category`, or `tasks.0.kind` (the kind of the first task) when your
   result has a list. "Some task is a trip to Riga" = an `any` check on `tasks`.

Then save. The case goes into `cases.jsonl`. The person who does the job today knows the expected answer best.
