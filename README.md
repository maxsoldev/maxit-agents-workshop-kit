# MaxIT agent kit

Build a small AI agent for a real office job, on your own laptop, and **measure** it while you build it.
Your team picks the service, writes the cases first, then makes the score go up.

- First time? **[SETUP.md](SETUP.md)**, part "Before the day".
- Setup done? **[docs/tutorial.md](docs/tutorial.md)** (30 min).
- The day: **[docs/day-plan.md](docs/day-plan.md)**. The method: **[docs/the-loop.md](docs/the-loop.md)**.

```text
   write a case  ──►  run the eval  ──►  read the failures and traces
        ▲                                         │
        └──────────  change ONE thing  ◄──────────┘
```

## The two use cases

| Use case | The job today | Folder |
|---|---|---|
| **Marketing inbox** | The bloxberg info inbox: sort emails, check unknown senders, draft replies, keep the contact sheet | `usecases/marketing/` |
| **Events and travel** | Office management: one overview of trips and leave from emails and chat, options, reminders | `usecases/travel/` |

## The three engines

The scaffold does the same small job on each engine, so you can compare them.

| Engine | What you change |
|---|---|
| **n8n** (:5702) | the workflow `Kit: <uc> workflow` in the n8n editor. Click **Publish**, then `make n8n-export` (commit `n8n/workflow.json`) |
| **Claude Code** (your login) | `claude/CLAUDE.md`, skills in `claude/.claude/skills/` |
| **Python** (OpenAI SDK) | `python/agent.py` |

All three use the same tools (`kit/tools.py`, :5701). No tool sends or books anything: the agent only
proposes, a person decides.

To compare: `Kit: <uc> agent (example)` in n8n is the same job as one AI Agent node (the model picks the
tools). Run it as engine `n8n-agent`.

## Repo map

```text
Makefile                   make up · setup · serve · check · eval · n8n-export · down · test · fmt · lint
.env.example               settings template: copy to .env
usecases/<uc>/             YOUR TEAM OWNS THIS FOLDER
  BRIEF.md                 the business problem; you write your design in it
  data/                    emails, contacts, events, the owner's rules (.md)
  cases.jsonl              the eval cases (edit them in the UI or by hand)
  output.schema.json       the shape of the result
  n8n/  claude/  python/   the three scaffolds
kit/                       the shared kit: UI, runner, tools, setup
runs/                      one JSON file per eval run (not in git)
docs/                      tutorial, the loop, day plan, contracts, data pack
```

The UI: `make serve`, then http://localhost:5700. Without the UI: `make eval UC=marketing ENGINE=claude`.

## How to take it to production (short)

- **Inputs:** an Outlook rule that collects mails in a folder, then read access to the mailbox or the
  Mattermost API. Read access and drafts only.
- **Human in the loop:** the agent proposes, a person sends, books and saves. Put this in code (no send tool),
  not only in the prompt.
- **Data and rules:** follow the MPG rules for AI agents (`docs/data-pack.md`).
- **Keep the evals:** run them on each change of the prompt, model or tools. Add a case for every mistake in
  real use.
- **Own it:** the fork is yours after the day.
