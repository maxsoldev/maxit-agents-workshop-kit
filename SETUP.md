# Setup

Two parts: **before the day** (at home, about 30 min) and **on the day** (about 15 min).
Each step has a **Check**. If the check fails, read **If it goes wrong**, or ask your trainer.

> **If you only use the UI** (no terminal): you need no setup. A developer in your team does the steps on one
> laptop and runs `make serve`. Open http://localhost:5700 on that laptop: **Rules**, **Cases**, **Evals**, **Try**.

## Before the day

### Step 1. Docker

Install Docker Desktop (macOS, Windows) or Docker Engine + Compose (Linux). Start it.
Docker Desktop → Settings → Resources → Memory: **8 GB** on a 16 GB laptop, 6 GB at least.

| Laptop RAM | What to do |
|---|---|
| 12 GB or more | Keep everything local. On 12 GB, close other heavy apps. |
| 8 GB | Set `LANGFUSE=off` or `LANGFUSE=hosted` in `.env` (see "Langfuse" below). Docker memory: 3 GB. |

- **Check:** `docker compose version` prints v2.20 or newer.
- **If it goes wrong:** on Windows, use WSL 2 and run everything inside WSL. No Docker at all: tell your
  trainer (see "Backups").

### Step 2. uv (Python)

Install uv: https://docs.astral.sh/uv/getting-started/installation/ (it gets Python 3.12 for you).

- **Check:** `uv --version` prints a version.
- **If it goes wrong:** open a new terminal.

### Step 3. Node and Claude Code

Install Node 20 or newer, then `npm install -g @anthropic-ai/claude-code`. Run `claude` once and log in with
your own Claude account.

- **Check:** `claude -p "say ok"` prints an answer.
- **If it goes wrong:** without Claude Code you can still use n8n and Python.

### Step 4. An OpenAI API key

Get a key from https://platform.openai.com/api-keys with a few EUR of credit.

- **Check:** the key starts with `sk-`. Never commit it.

### Step 5. Fork and clone the repo

Fork this repo on GitHub (one fork per team), then:

```bash
git clone <your fork URL> maxit-agents-workshop-kit
cd maxit-agents-workshop-kit
```

- **Check:** `ls usecases` shows `marketing` and `travel`.

### Step 6. Download the images

```bash
cp .env.example .env
docker compose --profile n8n --profile langfuse pull
uv sync
```

- **Check:** the pull ends without errors. `uv sync` ends with "Installed ... packages".
- **If it goes wrong:** a slow network is fine, let it run.

## On the day

Run all commands in the repo folder.

### Step 1. Settings

Open `.env` (not `.env.example`) and set `OPENAI_API_KEY=sk-...`. Set `TRAINS_URL` only if your trainer gives
you one. Your team skips an engine? Set for example `KIT_ENGINES=claude,python`.

- **Check:** `grep -c OPENAI_API_KEY=sk- .env` prints `1`.

### Step 2. Start n8n and Langfuse

```bash
make up
```

- **Check:** it ends with "Up (...). Next: make setup". http://localhost:5702 shows n8n, http://localhost:5703
  shows Langfuse.
- **If it goes wrong:** `make logs` shows the errors. Langfuse does not start: set `LANGFUSE=off` in `.env` and
  run `make up` again (you lose the traces, not the evals).

### Step 3. Set up n8n

```bash
make setup
```

It tests your key, makes the n8n login and imports the workflows. It is safe to run again.

- **Check:** the summary says OK for n8n and Langfuse and prints the logins. Log in to :5702 and :5703.
- **If it goes wrong:** read the FAIL line; it names the next step.
- **Later:** `make check`, `make eval` or the Evals page say that n8n has an older workflow (for example after
  `git pull`): run `make setup`. If you also changed it in n8n: `make setup FORCE=1` (your old copy goes to
  `runs/n8n-backup/`).

### Step 4. Start the kit

In a **second terminal** (keep it open all day):

```bash
make serve
```

- **Check:** http://localhost:5700 shows the kit UI with both use cases.
- **If it goes wrong:** "port ... is taken": another `make serve` or `make check` runs. Stop it (Ctrl+C).

### Step 5. Smoke check

```bash
make check
```

- **Check:** one `ANSWERS` line per engine and use case, and "All N engines answer" at the end.
  `2/3 checks pass` is fine: making the results right is the work of the day. Only an `ERROR` line is a problem.
- **If it goes wrong:** read the "Next:" text of the `ERROR` line. Claude: run `claude` once and log in. n8n:
  `make setup` again.

You are ready. Go to `docs/tutorial.md`.

## Langfuse: local, hosted or off

Set `LANGFUSE` in `.env`, then run `make up` and `make setup` again.

| Setting | What you get |
|---|---|
| `LANGFUSE=local` (default) | Langfuse in Docker on :5703, one trace per case |
| `LANGFUSE=hosted` | The trainer's Langfuse. Set `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` from your trainer |
| `LANGFUSE=off` | No traces. Evals work the same |

## Backups

- **No n8n on your laptop:** set `N8N=hosted`, `N8N_URL` and `N8N_API_KEY` (from your trainer) in `.env`, then
  `make setup`.
- **No Claude login:** use n8n and Python. **No OpenAI credit:** ask your trainer.
- **Start over:** `make reset` (it asks first), then `make up` and `make setup`.
