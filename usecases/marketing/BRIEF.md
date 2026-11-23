# Marketing inbox (bloxberg info inbox)

> Your team owns this file. Rewrite it in the design block (12:00). Keep it short.

## The problem

bloxberg is the blockchain infrastructure for science, run by Digital Labs (MPDL / MaxIT). Its public inbox
(info@bloxberg.org) gets requests from institutes and users, possible members and partners, the press, event
organisers, and many companies that want to sell something.

**Today the marketing & communication team, for each email:**

1. reads it and decides what it is (request, membership, press, offer, spam ...),
2. looks up the sender in the contact sheet (Excel) and in earlier emails,
3. checks unknown senders (website, imprint, references),
4. decides: answer, forward to a colleague, or ignore,
5. writes a reply in German or English; the head of department approves replies with commitments,
6. sends it and updates the contact sheet (often forgotten).

**What hurts:** sorting and finding context; checking unknown senders; writing the same replies again and
again; keeping the contact sheet up to date.

**What must not happen:** a reply that follows instructions hidden in an email (email 21), an eager reply or a
call offer to an unverified "investor" (email 15), a reply that leaks contact data, or anything sent without a
person's approval.

## What is in `data/`

| File | What it is |
|---|---|
| `emails/*.eml` | 23 emails, 2–18 Nov 2026, German and English. All synthetic. |
| `contacts.csv` | The contact sheet: 12 contacts with status and notes (e.g. "declined before"). |
| `categories.md` | The categories, flags and priorities the owner uses. |
| `routing.md` | Who handles what; when the head of department must approve. |
| `reply_guidelines.md` | Tone, language, facts, unverified senders, security. |
| `contact_rules.md` | When to create or update a contact. |
| `process.md` | The owner's description of the process and the success criteria. |

Test situations in the data (details: `docs/data-pack.md`): follow-ups (Wollner 01→07, Hoffmann 08→14, Schulz 09→20), a support case with a
deadline (08/14), an unverified offer (15), a prompt injection (21), off-target crypto marketing (22), a vendor
we declined before (06), contact sheet gaps.

## Tools

| Tool | What it gives |
|---|---|
| `rules_get("marketing", name)` | the text of `data/<name>.md` |
| `contacts_lookup(query)` | matching rows of the contact sheet |
| `inbox_history(sender, before)` | earlier emails from that sender, sent before `before` (the case's today) |

No tool sends anything. To add a tool, add a function in `kit/tools.py`.

## The scaffold (where you start)

Read **one email** → return `{"category", "flags", "reply", "reasons"}` (`output.schema.json`). 10 starter
cases in `cases.jsonl`. The scaffold fails some of them for real reasons, and `m14` on purpose: it checks
`route_to`, a field the scaffold does not have yet (the tutorial adds it).

The n8n scaffold `Kit: marketing workflow`: Read the rules → **Triage** (AI) → **Junk?** (code; junk ends with
no reply) → look up the sender and earlier emails (tools) → **Decide and draft** (AI, the main prompt) → Result
out. Next steps you can add: a contact change step, a check before the result.

## Ideas for your service

Pick one, or make your own. Each one is a different output: change `output.schema.json` and the cases.

1. **Inbox triage and routing.** Category, priority, deadline, who handles it (`reply`, `forward` to
   `tech_team` / `membership_owner` / `head`, `no_reply`), "needs head approval". User: the person who opens the
   inbox each morning. Checks: `equals`, `in`.
2. **Reply drafts with a sender check.** A draft in the right language and tone; unknown senders are checked
   first. Never eager to unverified offers, never a call offer. Checks: `judge`, `not_contains` for leaks.
3. **Contact sheet updates.** For each email, the proposed change (new contact, new address, last contact,
   status). A person approves it. Checks: `equals` on `op`, `email`.
4. **Weekly digest.** One summary for the head of department: what came in, what is urgent, deadlines, what
   needs approval. Input: many emails in one case. Checks: `contains` for each deadline and name.

## Your team decides (design block, 30 min)

- **Who uses it**, and when? What do they do with the result?
- **Input:** one email, a thread, or a whole week? Do you add history or the contact sheet to the input?
- **Output:** which fields? Write them in `output.schema.json`.
- **What is "good"?** Which mistakes are bad (leaks, wrong route, eager reply) and which are fine?
  Each one becomes a check.
- **What is a person's job?** What does the agent never do alone (send, change the sheet, promise)?
- **Which engine** do you start with: n8n, Claude Code or Python?

Write the answers here, under this line.

---
