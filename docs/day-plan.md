# Day plan (24 Nov 2026, end 18:00)

Times are timeboxes: at the end of a block, stop and show what you have. Each team has one trainer.

| Time | Block | At the end, your team has ... |
|---|---|---|
| 09:30 | **Kick-off and theory** | an idea of agents, tools, evals and the human in the loop |
| 11:00 | **Setup** (`SETUP.md`, "On the day") | `make check` runs on every laptop that builds. The trainer checks each team |
| 11:30 | **Tutorial** (30 min, `docs/tutorial.md`) | each person played the loop once on the travel scaffold: read a failure, fixed `t03` with one change, ran it twice, wrote one case from a failure |
| 12:00 | **Design** (30 min, hard stop) | the service written in `BRIEF.md`: who uses it, input, output, what "good" means, what a person always does. `output.schema.json` changed to fit. The business people say it, a developer types it (the UI has no editor for these files) |
| 12:30 | **Cases first** (30 min) | at least 5 cases in `cases.jsonl` with expected answers and checks, written by the business people. A new schema breaks the starter cases: change them to the new fields, or delete them. A first eval run on the scaffold (it fails: good) |
| 13:00 | Lunch | |
| 13:45 | **Build loop 1** | score → change one thing → score again. **Checkpoint 14:30:** the trainer asks for the score and the last change |
| 15:00 | Castle tour (about 1 h) | |
| 16:00 | **Build loop 2** | more cases for the failures you found, a better score. **Checkpoint 16:45** |
| 17:00 | **Freeze** | the last eval run is the one you show. Commit and push |
| 17:15 | **Demo** (5 min per team) | the service, the cases, the score before → after, one thing that surprised you |
| 17:45 | **Wrap-up** | how to take it to production (`README.md`); the fork is yours |
| 18:00 | End | |

## Roles in the team

- **Business people** (the owners of the job): decide what "good" means, write and fix the cases, read the
  results. They have the last word on what is right.
- **Developers:** change the agent, the tools and the schema. **Do not start building before the cases
  exist.**
- **Everyone:** one change at a time, and read the trace before you change the next thing
  (`docs/the-loop.md`).

## The demo (5 min)

1. The service: who uses it and what it does (1 min).
2. Two cases: one that works, one that was hard (2 min).
3. The score: first run → last run, and the change that helped most (1 min).
4. One surprise (1 min).
