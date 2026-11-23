# Events, business travel and office requests

> Your team owns this file. Rewrite it in the design block (12:00). Keep it short.

## The problem

The team travels to conferences, partner meetings and Max Planck institutes. **Martina, the office manager**,
books hotels and transport, keeps the year's event overview and is the substitute for everyone's leave.

**How it works today:** requests come in through different channels and she handles them one by one:

- The traveller files a travel request in SAP. After approval they get a "DRA" email with the travel number,
  which they sometimes forward to Martina.
- Separately they tell her by email or Mattermost where they go and what they need.
- She gets every leave approval, because she is the substitute.
- Training and procurement requests are still on paper.

**What hurts:** the same trip arrives in three places (email, forwarded DRA, Mattermost); updates and
cancellations get lost; trips without an approved request must not be booked; questions must reach travellers
before their leave starts; deadlines (registration, early bird, call for papers) are easy to miss.

**What must not happen:** a booking without an approved travel request, work on a cancelled trip, or anything
booked or sent without Martina.

## What is in `data/`

| File | What it is |
|---|---|
| `emails/*.eml` | 8 emails to office management: trip notes, forwarded DRA approvals, SAP leave approvals |
| `mattermost_messages.txt` | 5 Mattermost direct messages, copied from the chat window |
| `events.csv` | The annual event overview 2027 (event, place, dates, deadlines) |
| `travel_rules.md` | Order of steps, home base (Munich), train / flight / hotel limits |
| `priorities.md` | What comes first, one task per trip, task kinds and status words |
| `team.md` | Team directory (names, emails, Mattermost names) |
| `message_templates.md` | Templates for questions, reminders and cost estimates |
| `process.md` | The owner's description of the process and the open questions |

Built-in test situations (see `docs/data-pack.md`): Heidelberg in an email, a DRA and a Mattermost message (merge
into one task); Elena's DRA for Berlin without a note (link it to her hotel wish); the Mainz day trip without a
travel request (do not book, remind); Priya announces Riga, then cancels; a cost estimate for Brazil before any
request; leave over Christmas and in April; a paper training request mentioned only on Mattermost.

## Tools (kit MCP server, `docs/contracts.md` §7)

| Tool | What it gives |
|---|---|
| `rules_get("travel", name)` | the text of `data/<name>.md` |
| `events_list(date_from, date_to)` | events from the event overview |
| `trains_search(from_place, to_place, date, arrive_by / depart_after, modes)` | up to 5 real train connections on that day (times, trains, changes; **no prices**). `arrive_by="10:00"`: the last ones before 10:00. Trains only, unless `modes="all"` |

No tool books or sends anything. To add a tool (e.g. hotels), add a function in `kit/tools.py`.

## The scaffold service (where you start)

Read **one email or one Mattermost message** → return
`{"tasks": [{"kind", "person", "event", "date", "status"}], "reasons"}` (`output.schema.json`). The same small
job runs on n8n, Claude Code and Python, so the first eval is fair. The 8 starter cases in `cases.jsonl` use
the built-in situations: leave (t01, and t08: questions before the leave), the Heidelberg trip from 3 sources
(t02, t06: one task), Elena's DRA without a note (t07: link it to the event overview), Mainz without a travel
request (t03), the Riga cancel (t04) and the Brazil estimate (t05). The scaffold fails some of them for real
reasons (for example it does not look at the event overview). One gap is on purpose: case `t03` checks
`draft`, a field the scaffold schema does not have yet. It fails with "missing" until you add the field
(schema and prompt). **A high first score means your cases are too easy: add harder ones.**

## Ideas for your service

Pick one, or make your own. Each one is a different output, so change `output.schema.json` and the cases.

1. **One task list from all sources.** Input: all emails and messages of a week. Output: one task per trip
   (merged), with travel number yes/no, status, open questions; cancellations applied. Checks: `equals` on the
   status of each trip, "no duplicates".
2. **Train and hotel options per trip.** For a trip with a travel number: 2–3 train options from
   `trains_search` that follow the rules (2nd class, arrive before the meeting), and a hotel budget. The tool
   gives times and trains, **no prices**: Martina checks the price when she books. Checks: `contains` / `in` on
   times and trains, `not_contains` on `options.*.trains` (no buses), a `judge` for "follows the rules".
3. **Reminders before leave.** Who is away when; which questions must go out before the leave starts; a
   draft for each one (in the traveller's language, to the address from `team.md`). Checks: `equals` on
   `send_before`, a `judge` on the draft.
4. **Cost estimate.** For a possible trip (e.g. Open Repositories in Brazil): flight, hotel and fees, with
   the open questions, as a draft for the traveller. Checks: `contains` for the dates, `judge` for "rough but
   honest".

## Your team decides (design block, 30 min)

- **Who uses it**, and when (each new message, every morning, once a week)? Where does she want to see it?
- **Input:** one message, or all sources at once? Which sources arrive in practice?
- **Output:** which fields? Write them in `output.schema.json`.
- **What is "good"?** Which mistakes are bad (booking without a request, work on a cancelled trip, a
  reminder after the leave started)? Each one becomes a check.
- **What is a person's job?** What does the agent never do alone (book, send, decide who goes)?
- **Which engine** do you start with: n8n, Claude Code or Python?

Write the answers here, under this line.

---
