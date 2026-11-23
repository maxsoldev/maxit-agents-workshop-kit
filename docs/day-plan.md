# Day plan (24 Nov 2026, end 17:30)

Times are timeboxes: at the end of a block, stop and show what you have. Each team has one trainer.
After the workshop: castle tour 17:30–18:30, then dinner.

| Time | Block | At the end, your team has ... |
|---|---|---|
| 09:30 | **Kick-off and theory** (70 min) | an idea of agents, tools, evals and the human in the loop |
| 10:40 | Coffee (20 min) | |
| 11:00 | **n8n and the loop: a worked example** (30 min) | seen a simple n8n workflow and the kit UI |
| 11:30 | **Setup** (30 min, `SETUP.md`) | `make check` runs on every laptop that builds |
| 12:00 | **Tutorial** (30 min, `docs/tutorial.md`) | each person played the loop once |
| 12:30 | Lunch | |
| 13:30 | **Design and brainstorm** (45 min) | one engine, the service in `BRIEF.md`, the schema, 5 cases, a first eval run |
| 14:15 | **Build loop 1** | a better score. **Checkpoint 15:00** |
| 15:30 | Coffee (20 min) | |
| 15:50 | **Build loop 2** | more cases, a better score. **Checkpoint 16:25** |
| 16:55 | **Freeze** | the run you show, committed and pushed |
| 17:00 | **Demo** (2 teams × 5 min) | see below |
| 17:10 | **Wrap-up** (20 min) | how to take it to production (`README.md`) |
| 17:30 | End | castle tour 17:30–18:30, then dinner |

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
