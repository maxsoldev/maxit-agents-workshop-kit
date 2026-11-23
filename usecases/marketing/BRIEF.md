# Marketing inbox (bloxberg info inbox)

> Your team owns this file. Rewrite it in the design block (12:00). Keep it short.

## The problem

bloxberg is the blockchain infrastructure for science, run by Digital Labs (MPDL / MaxIT) with research
institutions worldwide. Its public inbox (info@bloxberg.org) gets requests from institutes and users, possible
members and partners, the press, event organisers, and many companies that want to sell something.

**Who does the work today:** the marketing & communication team. For each email a person:

1. reads it and decides what it is (request, membership, press, offer, spam ...),
2. looks up the sender in the contact sheet (Excel) and in earlier emails,
3. checks unknown senders (website, imprint, references),
4. decides: answer, forward to a colleague, or ignore,
5. writes a reply in German or English; the head of department approves replies with commitments,
6. sends it and updates the contact sheet (often forgotten).

**What hurts:** sorting and finding context for each email; checking unknown senders; writing the same replies
again and again; keeping the contact sheet up to date.

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

Built-in test situations (see `docs/data-pack.md`): follow-ups (Wollner 01→07, Hoffmann 08→14, Schulz 09→20),
a support case with a deadline (08/14), an unverified offer (15), a prompt injection (21), off-target crypto
marketing (22), a vendor we declined before (06), contact sheet gaps (new senders, missing or old addresses).

## Tools (kit MCP server, `docs/contracts.md` §7)

| Tool | What it gives |
|---|---|
| `rules_get("marketing", name)` | the text of `data/<name>.md` |
| `contacts_lookup(query)` | matching rows of the contact sheet |
| `inbox_history(sender, before)` | earlier emails from that sender; `before=<today>` keeps out emails after the case's today |

No tool sends anything. To add a tool, add a function in `kit/tools.py`.

## The scaffold service (where you start)

Read **one email** → return `{"category", "flags", "reply", "reasons"}` (`output.schema.json`). The reply can
be empty. The same small job runs on n8n, Claude Code and Python, so the first eval is fair. The 10 starter cases
in `cases.jsonl` use the built-in situations: follow-ups (m07, m14, m20), deadlines (m01, m12, m14, m20), the
declined vendor (m06, a contact sheet note), contact sheet notes in the reply (m01, m12), the membership owner
(m23), and the safety cases (m15, m21, m22).
The scaffold fails some of them for real reasons (for example a contact sheet note it does not use). One gap is
on purpose: case `m14` checks `route_to`, a field the scaffold schema does not have yet. It fails with
"missing" until you add the field (schema and prompt). **A high first score means your cases are too easy:
add harder ones.**

The n8n scaffold (`Kit: marketing workflow`) is a **workflow**, like the slides: code decides the path, the
model fills in the steps. Case in → **Read the rules** (tool) → **Triage** (AI: category, flags, sender) →
**Junk?** (code: spam, newsletter, phishing or off_target → **Result: no reply**, no more model calls) →
**Look up the sender in the contact sheet** and **Find earlier emails from the sender** (tools, plain HTTP) →
**Decide and draft** (AI: the result; the main prompt) → Result out. Case in has a pinned sample case (m14):
**Execute** in the editor runs it and shows the data on every node. Next steps from the slides: a Contact change step, a check before the result.
`Kit: marketing agent (example)` is the same job as one AI Agent node (the model picks the tools): engine
`n8n-agent`, to compare.

## Ideas for your service

Pick one, or make your own. Each one is a different output, so change `output.schema.json` and the cases.

1. **Inbox triage and routing.** Category, priority, deadline, and who handles it (`reply`, `forward` to
   `tech_team` / `membership_owner` / `head`, `no_reply`), plus "needs head approval". Users: the person who
   opens the inbox each morning. Checks are easy to write (`equals`, `in`).
2. **Reply drafts with a sender check.** A draft in the right language and tone, and a check of unknown
   senders first (contact sheet, earlier emails). Never eager to unverified offers, never a call offer. Checks:
   `judge` rubrics, `not_contains` for leaks.
3. **Contact sheet updates.** For each email, the proposed change: new contact, new address, last contact
   date, status. A person approves it. Checks: `equals` on fields like `op` and `email`.
4. **Weekly digest.** One summary for the head of department: what came in, what is urgent, which deadlines,
   what needs approval. Input: many emails in one case. Checks: `contains` for each deadline and name.

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
