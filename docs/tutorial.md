# Tutorial (30 min): the loop once, on the marketing scaffold

Like a game: first the tutorial, then the open world. You play the loop once on the **marketing** use case
(the bloxberg info inbox, the example in the slides), with small and safe steps. After this, your team designs
its own service (the design block).

- **Everyone does it**, on their own laptop, also if your team works on travel.
- **Pick one engine** (n8n, Claude or Python). The steps are the same for all three. Only the place of the prompt
  is different:

| Engine | The prompt is in | How to save a change |
|---|---|---|
| Python | `usecases/marketing/python/agent.py`, the text `PROMPT = """..."""` | save the file |
| Claude | `usecases/marketing/claude/CLAUDE.md`, parts "Steps" and "Answer" | save the file |
| n8n | n8n editor (http://localhost:5702, log in with `N8N_OWNER_EMAIL` and `N8N_OWNER_PASSWORD` from `.env`), workflow `Kit: marketing workflow`, node **Decide and draft** (the last AI step) → Chat Messages → **Message** (the steps). The node **Triage** has its own, smaller prompt | n8n saves your edit as a draft. Click **Publish** (top right): the eval runs only the published version. Then `make n8n-export UC=marketing` |

You need `make serve` running and the kit UI open: http://localhost:5700 → **marketing**.

## Level 1. Read a failure (about 8 min)

**What you do**

1. Open **Run**. Pick your engine. Tick no case (no tick = all cases). Run name `scaffold`. Click **Run eval**.
   The same from the terminal: `make eval UC=marketing ENGINE=python NAME=scaffold` (or `ENGINE=claude`,
   `ENGINE=n8n`).
2. When it ends, click **Open results →**. Open a red case (✗): click its row.
3. Read it from top to bottom: the **checks** (✓ and ✗, each with a reason), the **Output** next to the
   **Expected**, and the **Input**. Also the **Tool calls** (all engines: what the agent looked up, with the
   results). Claude and Python: also the **Engine log**; with Langfuse on, the **Langfuse trace** link. n8n:
   the **n8n execution ↗** link opens the run in n8n.

**What you see**

- A score such as `7/10 cases passed`, and one "first failing reason" per red case.
- `m14` is red on all engines. Its first failing reason: ``route_to` is missing in the result``. In the
  terminal: ``✗ m14 ... equals route_to: `route_to` is missing in the result``.
- `m01` and `m12` are often red too (always on Python, mostly on n8n, now and then on Claude):
  ``contains reply: does not have Direktorenkonferenz`` (`m01`) and ``contains reply: does not have webinar``
  (`m12`). Open the **Tool calls**: `contacts_lookup` found the note ("Met at Direktorenkonferenz", "Joined
  bloxberg webinar 2026"), and the agent often writes it in `reasons`, but the reply does not use it
  (`data/contact_rules.md`: "Read the notes before you draft ... and use them"). That is a real mistake of the
  agent, and one prompt line fixes it.

**What it teaches**

For each red case ask: **is the agent wrong, or is the case wrong?** A case can be wrong too (a bad expected
value, a check that is too strict). Only the business people can decide what is right. Write down one case
where the agent is wrong, and why.

## Level 2. Fix it with one change (about 10 min)

`m14` (Hoffmann writes again: her certificate is still not verified) checks a field `route_to`: who must handle
the email. Here it is the technical team (`tech_team`, see `data/routing.md`). The scaffold has no such field.
This gap is on purpose (`usecases/marketing/BRIEF.md`).

**What you do**

1. In `usecases/marketing/output.schema.json`, add `route_to` to `"required"` and to `"properties"`:

   ```json
   "required": ["category", "flags", "reply", "reasons", "route_to"],
   ```

   ```json
   "route_to": {
     "type": "string",
     "enum": ["", "tech_team", "membership_owner", "head"],
     "description": "Who must handle the email (data/routing.md). Empty when we reply ourselves."
   }
   ```

   (Put a comma after the `"reasons": {...}` block, before `"route_to"`.)
2. Add **one line** to the prompt of your engine, after the line about `reasons`:

   ```text
   6. Write route_to: who must handle the email (tech_team, membership_owner or head, see routing).
      When we reply ourselves, write "route_to": "". Never leave the field out.
   ```

   All 3 engines get the schema with each case and must answer in its shape, so the line is enough.
   n8n: the schema needs no edit in n8n (the node **Result shape** reads it from the case). Open the node
   **Decide and draft**, Chat Messages → **Message**, and put the line after step 5 (`5. Write reasons: ...`).
   Close the node, click **Publish** (top right; the Run page of the kit warns while an edit is not
   published), and run `make n8n-export UC=marketing`. Junk emails do not reach this node: the code node
   **Result: no reply** fills `route_to` with `""` by itself.
3. Run again, run name `route`. On the Results page, check **Compare with**: it shows your `scaffold` run (pick
   it if not).

**What you see**

- `m14` no longer fails on "missing": open it, the check `equals route_to` is ✓ (`got tech_team`).
- If the other checks of `m14` pass too, Compare shows **▲ better** for `m14`. It can stay red on another check,
  often the judge on the reply. That is a real mistake in the reply, not in `route_to`.
- A case can also get **▼ worse**: for example, the judge gives the reply 3 this time. That is the next thing
  to look at, not a reason to stop.
- With Langfuse on: the same compare in Langfuse. Click **Open this experiment in Langfuse** on the results page.

**What it teaches**

The loop: **one change, then run again, then compare**. The schema says *what* comes out; the prompt says
*how*. A change in one place often needs the other. Change one thing at a time, so you know what helped.

## Level 3. Run it twice (about 5 min)

**What you do**

Run again with **no change**, run name `route-2`. Compare `route-2` with `route`.

**What you see**

Some cases can flip: green before, red now, or the other way. In our runs `m12` flipped on n8n and Claude
(the reply used the webinar note one time and not the next), `m01` on Claude, and `m23` once on n8n (its
`judge` on the reply gave 3, then 4). Python gave the same result twice. Nothing changed: the model is not the
same each time.

**What it teaches**

**Trust a result only when it is the same twice.** If one change makes 1 case better, run again before you
keep it. A case that flips is a sign: the instruction is not clear, or the check is too strict.

## Level 4. A failure becomes a case (about 7 min)

**What you do**

1. Pick one mistake you saw in levels 1 to 3, for example a wrong `category`, a wrong `route_to`, or a reply
   that misses a fact.
2. Open **Cases → + Add case**, or **Copy** on a case with the same input (for example **Copy** on `m23`, the
   membership request). Fill in: an id (for example `m30`), a title that names the mistake, `Today`, the input
   (pick the email from `data/`), the **Expected** values (for example `{"route_to": "membership_owner"}`), and
   one or two **checks** for that mistake (for example `equals` on `route_to`, value empty = use Expected).
   Do **not** tick holdout yet.
3. **Save case**. Run only this case (on **Run**, tick only `m30`).

**What you see**

The new case in the list, and its result: it fails if the agent still makes the mistake.

**What it teaches**

**Every failure you find becomes a case.** Then the mistake cannot come back without the score showing it.
The cases are the part of the work that stays when the prompt and the model change.

## After the tutorial: keep or undo?

**Keep everything** (the `route_to` field, the prompt line, your new case). Commit it on your laptop:
`git add -A usecases/marketing && git commit -m "tutorial"`. If your team works in one fork, one person pushes
the tutorial commit; the others undo their copy (`git checkout -- usecases/marketing`) and `git pull`.

Your team starts its own service in the design block. If the design changes the schema, change or delete the
marketing cases that no longer fit (the cases-first block says how).
