# Repo rules (MaxIT agent kit)

- Participants fork this repo and change it. Keep the kit code (`kit/`) small and easy to read: they will.
- The seams are in `docs/contracts.md`. Do not change them in one part only.
- Settings only through `kit/config.py`. No secrets in git: `.env` is ignored; `.env.example` has placeholders.
- Plain English, short sentences (about 80 % of the way to ASD-STE100), in docs, comments and UI text.
- Docstrings: short, up to 5 lines, only where the code is not obvious.
- Python 3.12, `uv`. Tests: `uv run pytest -q`.
- Agent handoffs go to `.claude/handoffs/` (gitignored). Worktrees go to `.agents/worktrees/` (gitignored).
