# MaxIT agent kit

Build a small AI agent for a real office job, on your own laptop, and **measure** it while you build it.

The kit gives you two use cases with real (synthetic) data, a few tools, three ways to build the agent, and an
eval loop. It does not give you the answer: **your team picks the service**, writes the cases first, and then
makes the score go up.

- First time? Read **[SETUP.md](SETUP.md)**, part "Before the day".
- The day: **[docs/day-plan.md](docs/day-plan.md)**. The method: **[docs/the-loop.md](docs/the-loop.md)**.

## The idea of the day

1. **Pick the service.** Read the brief of your use case (`usecases/<uc>/BRIEF.md`). Decide who uses the
   service, what goes in, what comes out, and what "good" means.
2. **Cases first.** Write at least 5 cases: an input, the expected answer, and the checks. The business
   people on the team write them. Run the first eval on the scaffold. It fails: good, now you see the gap.
3. **The loop.** Change one thing, run the eval, read the result and the trace. Keep the change if the score
   goes up. Repeat.

## The loop

```text
   write a case  ──►  run the eval  ──►  read the failures and traces
        ▲                                         │
        └──────────  change ONE thing  ◄──────────┘
```

Every failure you find becomes a new case. The score only counts if the cases are honest.

## The two use cases

| Use case | The job today | Folder |
|---|---|---|
| **Marketing inbox** | The bloxberg info inbox: sort emails, check unknown senders, draft replies, keep the contact sheet | `usecases/marketing/` |
| **Events and travel** | Office management: one overview of trips and leave from emails and chat, options, reminders | `usecases/travel/` |

## The three engines

The scaffold does the same small job on each engine, so you can compare them fairly.

| Engine | What you change | How the kit calls it |
|---|---|---|
| **n8n** (Docker, :5702) | the workflow `Kit: <uc> agent` in the n8n editor (export it to `n8n/workflow.json` to keep it in git) | `POST /webhook/<uc>` |
| **Claude Code** (your login) | `claude/CLAUDE.md`, skills in `claude/.claude/skills/` | `claude -p` in `usecases/<uc>/claude/` |
| **Python** (OpenAI SDK) | `python/agent.py` | `uv run python agent.py`, JSON in on stdin, JSON out on stdout |

All three use the same tools through the kit MCP server (`kit/tools.py`, :5701). No tool sends or books
anything: the agent only proposes, a person decides.

## Repo map

```text
README.md  SETUP.md        start here
Makefile                   make up · make setup · make serve · make check · make eval · make down
docker-compose.yml         n8n and Langfuse (profiles n8n, langfuse)
.env.example               settings: copy to .env
usecases/<uc>/             YOUR TEAM OWNS THIS FOLDER
  BRIEF.md                 the business problem; you write your design in it
  data/                    emails, contacts, events, the owner's rules (.md)
  cases.jsonl              the eval cases (edit them in the UI or by hand)
  output.schema.json       the shape of the result
  n8n/workflow.json        n8n scaffold
  claude/                  Claude Code scaffold: CLAUDE.md, .mcp.json, .claude/
  python/agent.py          Python scaffold
kit/                       the shared kit: UI, runner, tools, setup (keep it simple)
runs/                      one JSON file per eval run (not in git)
docs/                      contracts, the loop, day plan, data pack
```

The UI (`make serve`, then http://localhost:5700) shows the cases, runs evals and compares runs. The same
runner works without the UI: `make eval UC=marketing ENGINE=claude`.

## How to take it to production (short)

- **Inputs:** today you read files from `data/`. Next: an Outlook rule that collects mails in a folder, then
  read access to the mailbox (IMAP/EWS) or the Mattermost API. Read access and drafts only.
- **Human in the loop:** keep "the agent proposes, a person sends, books and saves". Put this in code (no send
  tool), not only in the prompt.
- **Data and rules:** MPG rules for AI agents: low-risk use, data minimisation, a provider with a data
  processing agreement and no training on inputs (e.g. GWDG Chat AI), an audit log, no instructions from inside
  emails. See `docs/data-pack.md`.
- **Keep the evals:** the cases are your regression test. Run them on each change of the prompt, the model or
  the tools, and add a case for every mistake you see in real use.
- **Own it:** fork the repo. It is yours after the day.
