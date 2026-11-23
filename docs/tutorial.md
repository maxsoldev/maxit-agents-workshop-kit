# Tutorial (30 min): the loop once, on the marketing scaffold

You play the loop once on the **marketing** use case (the bloxberg info inbox), in small, safe steps.
**Everyone does it** on their own laptop, also travel teams. **Pick one engine.** The steps are the same; only
the place of the prompt is different:

| Engine | The prompt is in | How to save a change |
|---|---|---|
| Python | `usecases/marketing/python/agent.py`, the text `PROMPT = """..."""` | save the file |
| Claude | `usecases/marketing/claude/CLAUDE.md`, parts "Steps" and "Answer" | save the file |
| n8n | n8n editor (http://localhost:5702, login `N8N_OWNER_EMAIL` / `N8N_OWNER_PASSWORD` from `.env`), workflow `Kit: marketing workflow`, node **Decide and draft** → Chat Messages → **Message** | click **Publish** (top right; the eval runs only the published version), then `make n8n-export UC=marketing` |

You need `make serve` running and http://localhost:5700 → **marketing** open.

## Level 1. Read a failure (about 6 min)

**Do**

1. Open **Run**. Pick your engine. Tick no case (= all cases). Run name `scaffold`. Click **Run eval**.
   Terminal: `make eval UC=marketing ENGINE=python NAME=scaffold` (or `claude`, `n8n`).
2. Click **Open results →**, then the row of a red case (✗).
3. Read from top to bottom: the **checks** (each with a reason), **Output** next to **Expected**, **Input**,
   **Tool calls** (what the agent looked up).

**See**

- A score such as `6/10 cases passed`.
- `m12` (Sophie Martin asks for a case study) is red on Python and n8n: the reply does not refer to the earlier
  contact. In **Tool calls**, `contacts_lookup` found her note ("Joined bloxberg webinar 2026"), but the reply
  does not use it. `m01` has the same mistake. Claude often uses the note already: then pick another red case.
- `m14` is red on all engines: ``equals route_to: `route_to` is missing in the result``. That is level 3.

**Learn:** for each red case ask: **is the agent wrong, or is the case wrong?** Only the business people can
decide what is right.

## Level 2. Fix it with one prompt line (about 7 min)

**Do**

1. Add **one line** to the prompt, under step 4 (`4. Write a reply draft ...`). n8n: then **Publish** and
   `make n8n-export UC=marketing`.

   ```text
      If the sender's contact row has a note (a meeting, an event), refer to it in one short sentence.
   ```

2. Run again, run name `note`. On the results page, **Compare with** shows your `scaffold` run.

**See**

- `m12` is ▲ better ("we are glad you joined our webinar"). `m01` often too.
- Another case can get **▼ worse** (a judge flip). Look at it next; it is no reason to stop.

**Learn:** **one change, run again, compare.** The same line could also go into `data/reply_guidelines.md`:
then it is the owner's rule, for all engines at once.

## Level 3. A new field: schema + prompt (about 8 min)

`m14` (Hoffmann writes again: her certificate is still not verified) checks a field `route_to`: who must handle
the email (here `tech_team`, see `data/routing.md`). The scaffold has no such field, on purpose.

**Do**

1. In `usecases/marketing/output.schema.json`, add `route_to` to `"required"` and to `"properties"`
   (put a comma after the `"reasons": {...}` block):

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

2. Add **one line** to the prompt, after the line about `reasons` (n8n: after step 5 in **Decide and draft**,
   then **Publish** and `make n8n-export UC=marketing`; the schema needs no edit in n8n):

   ```text
   6. Write route_to: who must handle the email (tech_team, membership_owner or head, see routing).
      When we reply ourselves, write "route_to": "". Never leave the field out.
   ```

3. Run again, run name `route`. Compare with `note`.

**See:** in `m14`, the check `equals route_to` is ✓ (`got tech_team`). If its judge passes too, `m14` is
**▲ better**.

**Learn:** the schema says *what* comes out; the prompt says *how*. A new field needs both.

## Level 4. Run it twice (about 4 min)

**Do:** run again with **no change**, run name `route-2`. Compare `route-2` with `route`.

**See:** some cases can flip (green before, red now, or the other way), with no change.

**Learn:** **trust a result only when it is the same twice.** A flip means the instruction is not clear, or the
check is too strict.

## Level 5. A failure becomes a case (about 5 min)

**Do**

1. Pick one mistake from levels 1 to 4 (a wrong `category`, a wrong `route_to`, a reply that misses a fact).
2. **Cases → + Add case**, or **Copy** on a case with the same input (for example `m23`, the membership
   request). Fill in: id (for example `m30`), a title that names the mistake, `Today`, the input (an email from
   `data/`), **Expected** (for example `{"route_to": "membership_owner"}`), and one or two **checks** (for
   example `equals` on `route_to`; value empty = use Expected). Do **not** tick holdout.
3. **Save case**. On **Run**, tick only `m30` and run it.

**See:** the new case fails if the agent still makes the mistake.

**Learn:** **every failure becomes a case.** Then the mistake cannot come back without the score showing it.

## After the tutorial

**Keep everything** and commit it: `git add -A usecases/marketing && git commit -m "tutorial"`. One fork per
team: one person pushes; the others run `git checkout -- usecases/marketing` and `git pull`.
