# Tutorial (30 min): the loop once, on the travel scaffold

Like a game: first the tutorial, then the open world. You play the loop once on the **travel** use case, with
small and safe steps. After this, your team designs its own service (the design block).

- **Everyone does it**, on their own laptop, also if your team works on marketing.
- **Pick one engine** (n8n, Claude or Python). The steps are the same for all three. Only the place of the prompt
  is different:

| Engine | The prompt is in | How to save a change |
|---|---|---|
| Python | `usecases/travel/python/agent.py`, the text `PROMPT = """..."""` | save the file |
| Claude | `usecases/travel/claude/CLAUDE.md`, part "Steps" | save the file |
| n8n | n8n editor (http://localhost:5702), workflow `Kit: travel agent`, node **Agent**, Options → **System Message** | **Save** in n8n, then `make n8n-export UC=travel` |

You need `make serve` running and the kit UI open: http://localhost:5700 → **travel**.

## Level 1. Read a failure (about 8 min)

**What you do**

1. Open **Run**. Pick your engine, pick all cases, run name `scaffold`. Click **Run eval**.
   The same from the terminal: `make eval UC=travel ENGINE=python NAME=scaffold` (or `ENGINE=claude`, `ENGINE=n8n`).
2. When it ends, click **Open results →**. Open a red case (✗): click its row.
3. Read it from top to bottom: the **checks** (✓ and ✗, each with a reason), the **Output** next to the
   **Expected**, then the **Tool calls** and the **Engine log**. Claude and Python: the **Langfuse trace** link.
   n8n: the **n8n execution** link.

**What you see**

- A score such as `4/8 cases passed`, and one "first failing reason" per red case.
- The terminal line for `t03`: ``✗ t03 ... contains draft: `draft` is missing in the result``.
- Other red cases fail on a real mistake, for example `equals tasks.0.status: got open, expected ready_to_book`.

**What it teaches**

For each red case ask: **is the agent wrong, or is the case wrong?** A case can be wrong too (a bad expected
value, a check that is too strict). Only the business people can decide what is right. Write down one case
where the agent is wrong, and why.

## Level 2. Fix it with one change (about 10 min)

`t03` (Mainz day trip without a travel request) checks a field `draft`: a short message to Jonas that asks
for the travel request ("Reiseantrag"). The scaffold has no such field. This gap is on purpose
(`usecases/travel/BRIEF.md`).

**What you do**

1. In `usecases/travel/output.schema.json`, add `draft` to `"required"` and to `"properties"`:

   ```json
   "required": ["tasks", "reasons", "draft"],
   ```

   ```json
   "draft": {
     "type": "string",
     "description": "A short message to the traveller if they must do something first, else empty."
   }
   ```

   (Put a comma after the `"reasons": {...}` block, before `"draft"`.)
2. Add **one line** to the prompt of your engine, after the line about `reasons`:

   ```text
   5. Write draft: if the traveller must do something first (for example a missing travel request), a short
      message to them in their language (see message_templates). Else "".
   ```

   n8n: the schema needs no edit in n8n (the node **Result shape** reads it from the case). Only change the
   System Message, save, and run `make n8n-export UC=travel`.
3. Run again, run name `draft`. On the Results page, choose **Compare with** → your `scaffold` run.

**What you see**

- `t03` is green now, or it fails on the text of the draft and no longer on "missing".
- Compare shows **▲ better** for `t03`. A case can also get **▼ worse**: for example, the agent forgets the
  new field (``schema: the field `draft` is missing``). That is the next thing to fix, not a reason to stop.

**What it teaches**

The loop: **one change, then run again, then compare**. The schema says *what* comes out; the prompt says
*how*. A change in one place often needs the other. Change one thing at a time, so you know what helped.

## Level 3. Run it twice (about 5 min)

**What you do**

Run again with **no change**, run name `draft-2`. Compare `draft-2` with `draft`.

**What you see**

Some cases flip: green before, red now, or the other way. On n8n `t01` often flips. On Claude and Python,
cases with a `judge` check (`t05`, `t07`, `t08`) and `t06` can flip. Nothing changed: the model is not the
same each time.

**What it teaches**

**Trust a result only when it is the same twice.** If one change makes 1 case better, run again before you
keep it. A case that flips is a sign: the instruction is not clear, or the check is too strict.

## Level 4. A failure becomes a case (about 7 min)

**What you do**

1. Pick one mistake you saw in levels 1 to 3, for example a wrong `kind`, a wrong `status`, or a field the
   agent added.
2. Open **Cases → + Add case** (or **Copy** on a case with the same input). Fill in: an id (for example
   `t20`), a title that names the mistake, `Today`, the input (pick the email or message from `data/`), the
   **Expected** values, and one or two **checks** for that mistake (for example `equals` on `tasks.0.status`).
   Do **not** tick holdout yet.
3. **Save case**. Run only this case (on **Run**, pick only `t20`).

**What you see**

The new case in the list, and its result: it fails if the agent still makes the mistake.

**What it teaches**

**Every failure you find becomes a case.** Then the mistake cannot come back without the score showing it.
The cases are the part of the work that stays when the prompt and the model change.

## After the tutorial: keep or undo?

**Keep everything** (the `draft` field, the prompt line, your new case). Commit it on your laptop:
`git add -A usecases/travel && git commit -m "tutorial"`. If your team works in one fork, one person pushes
the tutorial commit; the others undo their copy (`git checkout -- usecases/travel`) and `git pull`.

Your team starts its own service in the design block. If the design changes the schema, change or delete the
travel cases that no longer fit (the cases-first block says how). Marketing teams: the tutorial changes are in
`usecases/travel/`, which you do not use. They do no harm.
