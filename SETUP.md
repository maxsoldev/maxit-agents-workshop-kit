# Setup

Two parts: **before the day** (at home, about 30 min, mostly downloads) and **on the day** (about 15 min).
Each step has a **Check**. If the check fails, read **If it goes wrong**, or ask your trainer.

> **If you only use the UI** (business people, no terminal): you need no setup of your own. A developer in
> your team does the steps below on one laptop and runs `make serve`. Then open http://localhost:5700 in the
> browser on that laptop: write cases (**Cases → Add case**), run evals (**Run**) and read the results
> (**Runs**). What you say about the service goes into `BRIEF.md`; the developer types it.

## Before the day

### Step 1. Docker

Install Docker Desktop (macOS, Windows) or Docker Engine + Compose (Linux). Start it.

**Memory (RAM).** With everything local, the Docker part uses about 4 GB: Langfuse about 3 GB, n8n about 0.5 GB.
Your browser, Claude Code and the kit need more on top of that.

| Laptop RAM | What to do |
|---|---|
| 16 GB or more | Comfortable. Keep everything local. |
| 12 GB | This works. Close other heavy apps. |
| 8 GB | Too little for local Langfuse. Set `LANGFUSE=off` or `LANGFUSE=hosted` in `.env` (see "Langfuse" below). |

Docker Desktop → Settings → Resources: set Memory to **8 GB** on a 16 GB laptop, and to 6 GB at least
(3 GB with `LANGFUSE=off`). Claude Code and the kit UI run outside Docker, so they do not count here.

- **Check:** `docker compose version` prints a version (v2.20 or newer).
- **If it goes wrong:** on Windows, use WSL 2 and run everything inside WSL. If your laptop cannot run Docker,
  tell your trainer: you will use the hosted n8n and Langfuse (see "Backups" below).

### Step 2. uv (Python)

Install uv: https://docs.astral.sh/uv/getting-started/installation/ . uv gets Python 3.12 for you.

- **Check:** `uv --version` prints a version.
- **If it goes wrong:** open a new terminal, so the `PATH` change is active.

### Step 3. Node and Claude Code

Install Node 20 or newer, then Claude Code: `npm install -g @anthropic-ai/claude-code`. Log in once with your
own Claude account: run `claude` and follow the steps.

- **Check:** `claude --version` prints a version, and `claude -p "say ok"` prints an answer.
- **If it goes wrong:** without Claude Code you can still use n8n and Python. Only the Claude engine will fail.

### Step 4. An OpenAI API key

Get an API key from https://platform.openai.com/api-keys with a little credit (a few EUR is enough for the day).
n8n, the Python agent and the `judge` checks use it.

- **Check:** you have a key that starts with `sk-`. Keep it secret: never commit it.

### Step 5. Fork and clone the repo

Fork this repo on GitHub (one fork per team is fine), then clone it:

```bash
git clone <your fork URL> maxit-agents-workshop-kit
cd maxit-agents-workshop-kit
```

- **Check:** `ls usecases` shows `marketing` and `travel`.

### Step 6. Download the images (saves time on the day)

```bash
cp .env.example .env
docker compose --profile n8n --profile langfuse pull
uv sync
```

- **Check:** the pull ends without errors. `uv sync` ends with "Installed ... packages".
- **If it goes wrong:** a slow network is fine, just let it run. On the day we have the backups.

## On the day

Run all commands in the repo folder.

### Step 1. Settings

```bash
cp .env.example .env      # skip if you did it before the day
```

Open `.env` and set `OPENAI_API_KEY=sk-...`. Your trainer gives you `TRAINS_URL`.
Put all your settings in `.env`, never in `.env.example`: that file is the template in git, and a change to it
stops `git pull`.
If your team does not use all engines, set them, e.g. `KIT_ENGINES=claude,python`: then `make up`, `make setup`
and `make check` skip n8n, and the Run page shows only these engines.

- **Check:** `grep -c OPENAI_API_KEY=sk- .env` prints `1`.

### Step 2. Start n8n and Langfuse

```bash
make up
```

- **Check:** it ends with "Up (...). Next: make setup". http://localhost:5702 shows n8n,
  http://localhost:5703 shows Langfuse.
- **If it goes wrong:** `make logs` shows the errors. If Langfuse does not start (it is the heavy part), set
  `LANGFUSE=off` in `.env` and run `make up` again. You lose the traces, not the evals.

### Step 3. Set up n8n and check Langfuse

```bash
make setup
```

This tests your model key with one cheap call (`GET /models` on OpenAI or on `OPENAI_BASE_URL`), creates the
n8n login, an API key, the OpenAI credential, and imports the workflows `Kit: marketing workflow` and
`Kit: travel workflow` (the scaffolds), and the examples `Kit: marketing agent (example)` and
`Kit: travel agent (example)`. It is safe to run again: if you changed a workflow in the n8n editor, setup keeps
your version and says so (and says when the kit has a newer file: then `make setup FORCE=1` loads it). Save your edits to git with `make n8n-export` (all use cases, or `UC=travel`); load the
file over them with `make setup FORCE=1` (the old n8n copy goes to `runs/n8n-backup/`). No key yet? Setup still imports the workflows (inactive, without
the credential), so you can look at them; it activates them when you run it again with the key.
After `git pull`, `make check`, `make eval` and the Run page warn when n8n has an older workflow than the kit:
run `make setup` (or `make setup FORCE=1` if you also changed it in n8n).

- **Check:** the summary at the end says OK for n8n and Langfuse and prints the logins. Log in to n8n
  (:5702) and Langfuse (:5703) with them.
- **If it goes wrong:** read the line that says FAIL; it names the next step. A changed OpenAI key: run
  `make setup` again.

### Step 4. Start the kit

In a **second terminal** (keep it open all day):

```bash
make serve
```

- **Check:** http://localhost:5700 shows the kit UI with both use cases. The tools server runs on :5701.
- **If it goes wrong:** "port ... is taken": another `make serve` (or a `make check`) still runs. Stop it (Ctrl+C) or close
  that terminal.

### Step 5. Smoke check

```bash
make check
```

This runs the first case of each use case on each engine (only the ones in `KIT_ENGINES`).

- **Check:** one `ANSWERS` line for each engine and use case, and at the end "All N engines answer".
  `ANSWERS ...: 2/3 checks pass` is fine: the engine gave a result, and the result is not right yet. Making
  the results right is the work of the day. Only an `ERROR` line is a problem: that engine gave no result.
- **If it goes wrong:** the `ERROR` line says which engine and why, and "Next:" says what to do. Claude: run
  `claude` once and log in. n8n: run `make setup` again. Python: the "Next:" text fits the error (the key,
  `OPENAI_BASE_URL`, or the model name `KIT_MODEL`).

You are ready. Go to `docs/tutorial.md`.

## Langfuse: local, hosted or off

Set `LANGFUSE` in `.env`, then run `make up` and `make setup` again.

| Setting | What you get |
|---|---|
| `LANGFUSE=local` (default) | Langfuse in Docker on :5703. One trace per case; Claude's model and tool calls nest under it. |
| `LANGFUSE=hosted` | The trainer's Langfuse. Set `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` of your team's project. |
| `LANGFUSE=off` | No traces and no trace links. Evals work the same. |

n8n steps are in Langfuse too: the runner reads each n8n execution and writes one span per node (with times,
input, output and the model's tokens). The full detail stays in **Executions** in n8n (the results page links to it).

## Backups

- **n8n hosted:** if n8n does not run on your laptop, set `N8N=hosted`, `N8N_URL` and `N8N_API_KEY` (from your
  trainer) in `.env`, then `make setup`. The kit UI, the runner, Claude and Python still run on your laptop.
- **No Claude login:** use n8n and Python. **No OpenAI credit:** ask your trainer.
- **Start over:** `make reset` deletes the n8n and Langfuse data (it asks first). Then `make up` and `make setup`.
