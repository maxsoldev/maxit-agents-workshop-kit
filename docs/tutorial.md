# Tutorial (30 min): the loop once, on email triage

You play the loop once on the **marketing** use case (the bloxberg info inbox), in small, safe steps.
**Everyone does it** on their own laptop, also travel teams. The job is small on purpose:

> **One email in → category + flags out.** For example: an institute asks for a demo → `institute_request`, no
> flags. "You have won 1,000,000 EUR" → `spam`, flag `phishing`.

**Pick one engine** (n8n, Claude or Python). The steps are the same on each. All the files you change are in the
kit UI: **Rules** → pick your engine.

| Engine | The prompt (Rules → **Instructions**) | The owner's rules (Rules → **Owner's rules**) |
|---|---|---|
| n8n | `prompts/triage.md` (node **Triage**). A save on the Rules page also pushes it to n8n. In the n8n editor (http://localhost:5702): node **Triage** → Chat Messages → **Message**, then **Publish** | `categories.md` |
| Claude | `CLAUDE.md`, part "Steps" | `categories.md` |
| Python | `prompt.md` | `categories.md` |

You need `make serve` running and http://localhost:5700 open, use case **Marketing**.

## Level 1. Read a failure (about 6 min)

**Do**

1. Open **Run**. Pick your engine. Tick no case (= all 10 cases). Run name `start`. Click **Run eval**.
   Terminal: `make eval UC=marketing ENGINE=python NAME=start` (or `claude`, `n8n`).
2. Click **Open results →**, then the row of a red case (✗).
3. Read from top to bottom: the **checks** (each with a reason), **Output** next to **Expected**, **Input**.

**See**

- A score such as `7/10 cases passed`.
- `m15` (an unknown fund wants to "tokenize your science") is red on most runs:
  ``equals flags: got ["unverified", "off_target"], expected ["unverified"]``. The category is right; the agent
  adds a second flag.
- `m08` (a support request with a deadline) is red: ``equals priority: `priority` is missing``. That is level 3.

**Learn:** for each red case ask: **is the agent wrong, or is the case wrong?** Open **Rules** → `categories.md`:
`off_target` is for an offer that "assumes we are a speculative token or crypto project". The fund talks about
tokens, so the agent has a point. Only the owner can say what is right. Here the owner says: `off_target` is for
people who want to *sell* us something.

## Level 2. Fix it with one rule line (about 7 min)

**Do**

1. Open **Rules** → your engine → `categories.md`. Under the `off_target` line, add **one line** (two spaces at
   the start keep it in the same list item):

   ```text
     Only for offers that want to sell us something. Someone who wants to fund us or partner with us is `unverified`, not `off_target`.
   ```

2. **Save**. The agent reads the rules at every case: nothing else to do, on every engine.
3. Run again, run name `off-target-rule`. On the results page, **Compare with** shows your `start` run.

**See**

- `m15` is ▲ better.
- `m22` (crypto marketing wants to list "our token") stays ✓ with `off_target`: the new rule did not break it.

**Learn:** **one change, run again, compare.** You changed the owner's rule, not the code: a business person can
make this change alone, in the Rules page.

## Level 3. A new field: schema + prompt (about 8 min)

`m08` checks a field `priority`. `categories.md` already says what is `high`, `medium` and `low`, but the agent
does not give a priority yet, on purpose.

**Do**

1. In `usecases/marketing/output.schema.json`, add `priority` to `"required"` and to `"properties"` (put a comma
   after the `"flags": {...}` block):

   ```json
   "required": ["category", "flags", "priority"],
   ```

   ```json
   "priority": {
     "type": "string",
     "description": "A priority from the priority list in the rules file categories.md."
   }
   ```

2. Add **one line** to the prompt (Rules → **Instructions**), after step 3:

   ```text
   4. Write priority: one priority from the priority list of the rules.
   ```

3. Run again, run name `priority`. Compare with `off-target-rule`.

**See:** in `m08`, the check `equals priority` is ✓ (`got high`). `m08` is **▲ better**.

**Learn:** the schema says *what* comes out; the prompt says *how*. A new field needs both. The words the owner
uses (categories, flags, priorities) stay in the rules file, not in the schema: the owner can add one there.

## Level 4. Run it twice (about 4 min)

**Do:** run again with **no change**, run name `priority-2`. Compare `priority-2` with `priority`.

**See:** a case can flip, with no change. Often it is `m21` (a partnership request with hidden text for AI): on
some runs the agent adds `phishing` next to `injection`.

**Learn:** **trust a result only when it is the same twice.** A flip means the rule is not clear (when is a
hidden instruction also `phishing`?), or the check is too strict. Ask the owner, then fix the rule or the case.

## Level 5. A failure becomes a case (about 5 min)

**Do**

1. Open **Try**. Pick your engine and an email that no case has yet, for example
   `data/emails/10_2026-11-09.eml` (a webinar reminder). Click **Run the agent**.
2. Is the answer right? Click **Make a case from this**. The case form opens with the email and the agent's
   answer as **Expected**. Fix Expected if the answer is wrong (a webinar reminder is a `newsletter`). Give an id
   (for example `m10`) and a title. Add the checks `equals` on `category` and `equals` on `flags`.
3. **Save case**. On **Run**, tick only `m10` and run it.

**See:** the case is green if the agent gets it right, red if it still makes the mistake.

**Learn:** **every failure becomes a case.** Then the mistake cannot come back without the score showing it.

## After the tutorial

**Keep everything** and commit it: `git add -A usecases/marketing && git commit -m "tutorial"`. One fork per
team: one person pushes; the others run `git checkout -- usecases/marketing` and `git pull`.

Your team's own service starts small in the same way, and grows step by step: more of the owner's rules are in
`usecases/<uc>/extend/` (routing, reply guidelines, travel rules ...), and `BRIEF.md` has ideas.
