# The loop: eval-driven development in one page

You do not know if a change makes the agent better until you measure it. So: **cases first, then build,
then measure every change.**

## The steps

1. **Write the cases.** Each case is one input, the expected answer, and the checks (`cases.jsonl`).
   Start with 5: 2 easy ones, 2 tricky ones, 1 that must never go wrong (a safety case).
2. **Run the eval** on the scaffold. It fails. Good: now you see the gap.
3. **Read the failures.** Open each failed case: the output, the reason of each check, the trace.
   Ask: is the agent wrong, or is the case wrong?
4. **Change one thing.** The prompt, one rule, one tool, the model, the schema. Only one.
5. **Run the eval again.** Compare with the last run (Results → Compare). Better: keep it and commit.
   Worse: undo it. Same: think again.
6. **Every new failure becomes a case.** When you see a mistake (in a trace, in a demo, in real use), write a
   case for it before you fix it. Then the fix stays fixed.

## Rules that save time

- **One change at a time.** With two changes you do not know which one helped.
- **Read the trace, not only the score.** The trace shows which tools the agent called, what it read and
  where it went wrong. Claude: Langfuse (the `tool:<name>` spans have the input and output; the results page
  lists the same tool calls). n8n: Executions (the results page links to each one). Python: the `tool ...` lines it logs on stderr (run
  `agent.py` by hand to see them; the error of a failed run shows the last lines).
- **Do not fit the cases.** A rule like "if the sender is Lara Venn, say unverified" makes the case pass and
  the agent worse. Write rules that hold for the next email too.
- **Run it twice** before you trust a small change. Models are not fully deterministic.
- **Fix the case** when the case is wrong. Ask the owner what is right; do not change it to make it pass.

## Which check when

| Check | Use it for | Example |
|---|---|---|
| `equals` | one right value | `category` is `newsletter` |
| `in` | a few right values | `category` is `partnership` or `press` |
| `contains` | a word in a text, an item in a list | `flags` has `injection`; the reply names "28" |
| `not_contains` | things that must never appear | no `@` in the reply (no leaked addresses) |
| `count` | how many items a list has | `tasks` has exactly 1 item (no duplicates): `{"equals": 1}` |
| `any` | "there is an item where ...", in any order | a task with `kind` `trip` whose `event` contains "Riga" |
| `judge` | quality you cannot check with a word: tone, "asks for details, no call" | rubric in one or two sentences |

The **schema** check always runs first: the result must match `output.schema.json`. A check on a field that is
not in the schema fails with "missing": add the field to the schema. The runner sends the schema to every engine
(`output_schema` in the engine input): the Claude agent, `agent.py` and the n8n **Result shape** node read it from
there. Say in the prompt what the new field means, if its name and description are not enough.

Prefer simple checks: they are fast, free and never wrong. Use `judge` for quality only, and write the rubric
like an instruction to a colleague: what must be in it, what must not.

## How business people write cases (in the UI)

Open http://localhost:5700 → your use case → **Cases** → **Add case**.

1. **Title:** what the situation is, in a few words ("Unknown fund wants a call").
2. **Input:** pick the email file (`data/emails/...`) or paste a message.
3. **Expected:** what a good colleague would answer, field by field (category, route, date, ...).
4. **Checks:** for each important point, one check. "Must never" points become `not_contains` or a `judge`.

Then save. The case goes into `cases.jsonl`, so git shows the change. A developer never has to write the
expected answer: the person who does the job today knows it best.
