# Day plan (24 Nov 2026, end 17:30)

Times are timeboxes: at the end of a block, stop and show what you have. Each team has one trainer.
After the workshop: castle tour 17:30–18:30, then dinner.

| Time | Block | At the end, your team has ... |
|---|---|---|
| 09:30 | **Kick-off and theory** (70 min) | an idea of agents, tools, evals and the human in the loop |
| 10:40 | Coffee (20 min) | |
| 11:00 | **n8n and the loop: a worked example** (30 min, trainers show it) | seen a simple n8n workflow (email triage: one email in → category + flags out), its business rules file, and the kit UI: Rules, Cases, Run, the results and one change that makes the score better |
| 11:30 | **Setup** (30 min, `SETUP.md`, "On the day") | the local workspace (n8n, Langfuse, the kit UI) on every laptop that builds: `make check` runs |
| 12:00 | **Tutorial** (30 min, `docs/tutorial.md`) | each person played the loop once on email triage: read a failure, change one rule, run again, compare |
| 12:30 | Lunch | |
| 13:30 | **Design and brainstorm** (45 min, hard stop) | **one engine** for the team; the service written in `BRIEF.md` ("Your team decides"); `output.schema.json` changed to fit; at least 5 cases with expected answers and checks, written by the business people (a new schema breaks the starter cases: change them or delete them); a first eval run (it fails: good). From now on the team changes only its engine's folder. The business people say it, a developer types the schema (the UI cannot edit it) |
| 14:15 | **Build loop 1** | score → change one thing → score again. **Checkpoint 15:00:** the score and the last change |
| 15:30 | Coffee (20 min) | |
| 15:50 | **Build loop 2** | more cases for the failures you found, a better score. **Checkpoint 16:25** |
| 16:55 | **Freeze** | the last eval run is the one you show. Commit and push |
| 17:00 | **Demo** (2 teams × 5 min) | see below |
| 17:10 | **Wrap-up** (20 min) | how to take it to production (`README.md`); the fork is yours |
| 17:30 | End (hard stop) | castle tour 17:30–18:30, then dinner |

## Roles in the team

- **Business people** (the owners of the job): decide what "good" means, write and fix the cases, read the
  results. They have the last word on what is right.
- **Developers:** change the agent, the tools and the schema. **Do not start building before the cases exist.**
- **Everyone:** one change at a time; read the trace before the next change (`docs/the-loop.md`).

## The demo (5 min)

1. The service: who uses it and what it does (1 min).
2. Two cases: one that works, one that was hard (2 min).
3. The score: first run → last run, and the change that helped most (1 min).
4. One surprise (1 min).
