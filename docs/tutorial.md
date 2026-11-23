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
| n8n | n8n editor (http://localhost:5702, log in with `N8N_OWNER_EMAIL` and `N8N_OWNER_PASSWORD` from `.env`), workflow `Kit: marketing workflow`, node **Decide and draft** → Chat Messages → **Message** | n8n saves your edit as a draft. Click **Publish** (top right): the eval runs only the published version. Then `make n8n-export UC=marketing` |

You need `make serve` running and the kit UI open: http://localhost:5700 → **marketing**.

## Level 1. Read a failure (about 6 min)

**What you do**

1. Open **Run**. Pick your engine. Tick no case (no tick = all cases). Run name `scaffold`. Click **Run eval**.
   The same from the terminal: `make eval UC=marketing ENGINE=python NAME=scaffold` (or `ENGINE=claude`,
   `ENGINE=n8n`).
2. When it ends, click **Open results →**. Open a red case (✗): click its row.
3. Read it from top to bottom: the **checks** (✓ and ✗, each with a reason), the **Output** next to the
   **Expected**, the **Input** and the **Tool calls** (what the agent looked up, with the results).

**What you see**

- A score such as `6/10 cases passed`, and one "first failing reason" per red case.
- `m12` (Sophie Martin asks for a case study) is red on Python and n8n: the judge on the reply says it does not
  refer to the earlier contact. Open the **Tool calls**: `contacts_lookup` found her note ("Joined bloxberg
  webinar 2026"), but the reply does not use it. `m01` has the same mistake (note "Met at
  Direktorenkonferenz"). Claude often uses the note already: then look at another red case.
- `m14` is red on all engines: ``equals route_to: `route_to` is missing in the result``. That is level 3.

**What it teaches**

For each red case ask: **is the agent wrong, or is the case wrong?** A case can be wrong too (a bad expected
value, a check that is too strict). Only the business people can decide what is right.

## Level 2. Fix it with one prompt line (about 7 min)

The scaffold reads the contact sheet, but its prompt does not say what to do with a note.

**What you do**

1. Add **one line** to the prompt of your engine, under step 4 (`4. Write a reply draft ...`):

   ```text
      If the sender's contact row has a note (a meeting, an event), refer to it in one short sentence.
   ```

   n8n: node **Decide and draft** → Chat Messages → **Message**; then **Publish** (top right) and
   `make n8n-export UC=marketing`. n8n agent example: the Agent node → Options → System Message.
2. Run again, run name `note`. On the Results page, check **Compare with**: it shows your `scaffold` run.

**What you see**

- `m12` is ▲ better: the reply now says something like "we are glad you joined our webinar". `m01` often too
  (it can stay red on another part of its rubric).
- Another case can get **▼ worse** (a judge flip). That is the next thing to look at, not a reason to stop.

**What it teaches**

The loop: **one change, then run again, then compare**. The same line could also go into
`data/reply_guidelines.md`: then it is the owner's rule, and all engines get it at once.

## Level 3. A new field: schema + prompt (about 8 min)

`m14` (Hoffmann writes again: her certificate is still not verified) checks a field `route_to`: who must handle
the email (here `tech_team`, see `data/routing.md`). The scaffold has no such field, on purpose.

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

   All 3 engines get the schema with each case, so the schema needs no edit in n8n. n8n: put the line after
   step 5 in **Decide and draft**, then **Publish** and `make n8n-export UC=marketing`.
3. Run again, run name `route`. Compare with `note`.

**What you see**

- `m14`: the check `equals route_to` is ✓ (`got tech_team`). If its judge on the reply passes too, `m14` is
  **▲ better**.

**What it teaches**

The schema says *what* comes out; the prompt says *how*. A new field needs both. Change one thing at a time, so
you know what helped.

## Level 4. Run it twice (about 4 min)

**What you do**

Run again with **no change**, run name `route-2`. Compare `route-2` with `route`.

**What you see**

Some cases can flip: green before, red now, or the other way. In our runs `m06` flipped on Python (the judge on
`reasons`), `m01` on Claude (once the reply claimed "only a hash is sent") and `m15` on n8n. Nothing changed: the model is not the
same each time.

**What it teaches**

**Trust a result only when it is the same twice.** A case that flips is a sign: the instruction is not clear,
or the check is too strict.

## Level 5. A failure becomes a case (about 5 min)

**What you do**

1. Pick one mistake you saw in levels 1 to 4, for example a wrong `category`, a wrong `route_to`, or a reply
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

**Keep everything** (the 2 prompt lines, the `route_to` field, your new case). Commit it on your laptop:
`git add -A usecases/marketing && git commit -m "tutorial"`. If your team works in one fork, one person pushes
the tutorial commit; the others undo their copy (`git checkout -- usecases/marketing`) and `git pull`.

Your team starts its own service in the design block. If the design changes the schema, change or delete the
marketing cases that no longer fit (the cases-first block says how).
