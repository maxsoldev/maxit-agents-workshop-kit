# Events, business travel and office requests

> Your team owns this file. Rewrite it in the design block (13:30, after lunch). Keep it short.

## The problem

The team travels to conferences, partner meetings and Max Planck institutes. **Martina, the office manager**,
books hotels and transport, keeps the year's event overview and is the substitute for everyone's leave.

**Today requests come in through different channels, and she handles them one by one:**

- The traveller files a travel request in SAP. After approval they get a "DRA" email with the travel number,
  which they sometimes forward to Martina.
- Separately they tell her by email or Mattermost where they go and what they need.
- She gets every leave approval, because she is the substitute.
- Training and procurement requests are still on paper.

**What hurts:** the same trip arrives in three places; updates and cancellations get lost; trips without an
approved request must not be booked; questions must reach travellers before their leave starts; deadlines
(registration, early bird, call for papers) are easy to miss.

**What must not happen:** a booking without an approved travel request, work on a cancelled trip, or anything
booked or sent without Martina.

## What is in `data/` (the inputs, shared by all engines)

| File | What it is |
|---|---|
| `emails/*.eml` | 8 emails to office management: trip notes, forwarded DRA approvals, SAP leave approvals |
| `mattermost_messages.txt` | 5 Mattermost direct messages, copied from the chat window |
| `events.csv` | The annual event overview 2027 (event, place, dates, deadlines) |

## Business rules: one file per engine

Each engine has **one business rules file**: its steps and the owner's rules (categories, kinds ...), plain
text, read on every case. Claude: `claude/CLAUDE.md` (Claude Code loads it). Python: `python/prompt.md`
(`agent.py` reads it). n8n: one file per AI step, e.g. `n8n/triage.md` (the node *Read the rules* gets it from
the tools server; no push). Edit them in the kit UI: **Rules** (pick your engine). Write rules as lists, one
item per line, not as tables.

The starter files (`claude/CLAUDE.md`, `python/prompt.md`, `n8n/read_message.md`) hold the steps and the kinds (trip, leave, estimate, reminder), how to write person, event and date, and the team.

More of the owner's rules, for when you extend the agent, are in `extend/rules/` (see `extend/README.md`;
paste what you need into your file):

- `priorities.md`: what comes first, one task per trip, status words.
- `travel_rules.md`: order of steps, home base (Munich), train / flight / hotel limits.
- `message_templates.md`: templates for questions, reminders and cost estimates.
- `process.md`: the owner's description of the process and the open questions.

Test situations in the data (details: `docs/data-pack.md`): Heidelberg in an email, a DRA and a Mattermost message (one task); Elena's DRA for
Berlin without a note (link it to her hotel wish); the Mainz day trip without a travel request (do not book,
remind); Priya announces Riga, then cancels; a cost estimate for Brazil; leave over Christmas and in April; a
paper training request mentioned only on Mattermost.

## Tools

| Tool | What it gives |
|---|---|
| `events_list(date_from, date_to)` | events from the event overview |
| `trains_search(from_place, to_place, date, arrive_by / depart_after, modes)` | up to 5 real train connections on that day (times, trains, changes; **no prices**). Trains only, unless `modes="all"` |

No tool books or sends anything. To add a tool (e.g. hotels), add a function in `kit/tools.py`.

## The scaffold (where you start)

**One email or Mattermost message in → kind, person, event, date out:**
`{"kind": "trip", "person": "Jonas Meier", "event": "E-Science-Tage, Heidelberg", "date": "2027-03-02"}`
(`output.schema.json`). 10 starter cases in `cases.jsonl`, all with `equals` or `contains` checks. The starter
agent passes most of them: your first job in the design block is harder cases (a cancelled trip, three messages
about one trip, a trip without a travel request).

The n8n scaffold `Kit: travel workflow`: Case in → Read the rules → **Read message** (AI, the prompt) → Read
result → Result out. Next steps you can add: look up the events (tool `events_list`), search trains, a status
per trip.

## Ideas for your service

Pick one, or make your own. Each one is a different output: change `output.schema.json` and the cases.

1. **One task list from all sources.** Input: all emails and messages of a week. Output: one task per trip
   (merged), with travel number yes/no, status, open questions; cancellations applied. Checks: `equals` on the
   status of each trip, "no duplicates".
2. **Train and hotel options per trip.** For a trip with a travel number: 2–3 train options from
   `trains_search` that follow the rules (2nd class, arrive before the meeting), and a hotel budget. No prices:
   Martina checks the price when she books. Checks: `contains` / `in` on times and trains, `not_contains` on
   `options.*.trains` (no buses), a `judge` for "follows the rules".
3. **Reminders before leave.** Who is away when; which questions must go out before the leave starts; a draft
   for each (in the traveller's language, to the address from the team list). Checks: `equals` on `send_before`, a
   `judge` on the draft.
4. **Cost estimate.** For a possible trip (e.g. Open Repositories in Brazil): flight, hotel and fees, with the
   open questions, as a draft for the traveller. Checks: `contains` for the dates, `judge` for "rough but
   honest".

## Your team decides (design block, 45 min with the first cases)

- **Who uses it**, and when (each new message, every morning, once a week)? Where does she want to see it?
- **Input:** one message, or all sources at once? Which sources arrive in practice?
- **Output:** which fields? Write them in `output.schema.json`.
- **What is "good"?** Which mistakes are bad (booking without a request, work on a cancelled trip, a
  reminder after the leave started)? Each one becomes a check.
- **What is a person's job?** What does the agent never do alone (book, send, decide who goes)?
- **Which engine** do you start with: n8n, Claude Code or Python?

Write the answers here, under this line.

---
