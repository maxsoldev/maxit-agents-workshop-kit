# Material to extend the agent (not used by the starter agent)

The starter agent does one small job. This folder has more of the owner's material for when your team adds a
step (see `BRIEF.md`, "Ideas for your service"):

- `rules/*.md`: more of the owner's rules. Paste the part you need into your engine's business rules file
  (`claude/CLAUDE.md`, `python/prompt.md`, or the `.md` file of an n8n step), under "Business rules".
- `claude-skills/`: example Claude Code skills, the way to extend the Claude agent with a new step. Copy a folder
  into `claude/.claude/skills/`, then add one line to the steps in `claude/CLAUDE.md`, e.g. "To check an unknown
  sender, use the skill `check-sender`".

Read the rules with the business people first: they decide if a rule is right.
