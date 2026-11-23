# Kit commands. Run `make help` for the list.
# `make up` starts n8n if N8N=local and Langfuse if LANGFUSE=local (both read from .env).

COMPOSE := docker compose
UC ?= marketing
ENGINE ?= n8n
CASES ?=
NAME ?=

.PHONY: help up down setup n8n-export check serve eval run-case logs reset test fmt lint

help:
	@echo "make up       start n8n and/or Langfuse in Docker (as set in .env), wait until healthy"
	@echo "make setup    first-time setup: n8n login, API key, OpenAI credential, workflows; Langfuse check"
	@echo "              keeps your n8n edits; FORCE=1 loads workflow.json over them"
	@echo "make n8n-export [UC=travel]  save your n8n editor changes to usecases/<uc>/n8n/workflow.json"
	@echo "make serve    kit UI on :5700 and tools server on :5701 (keep it running)"
	@echo "make check    one case per use case and engine: OK or what to fix"
	@echo "make eval UC=marketing ENGINE=n8n [CASES=m01,m02] [NAME=try-1] [HOLDOUT=1]"
	@echo "make run-case UC=marketing ENGINE=python CASE=m14   one case: input, output, log, checks"
	@echo "make test     run the kit tests"
	@echo "make fmt      format the Python code (ruff: layout and import order)"
	@echo "make lint     check format and code (ruff), change nothing"
	@echo "make logs     follow the Docker logs"
	@echo "make down     stop the containers (data stays)"
	@echo "make reset    stop the containers and DELETE their data (asks first)"

.env:
	@echo "No .env yet. Run: cp .env.example .env  (then set OPENAI_API_KEY)"; exit 1

up: .env
	@uv run --quiet python -m kit.setup services; \
	profiles="$$(uv run --quiet python -m kit.setup profiles)"; \
	if [ -z "$$profiles" ]; then \
		echo "Nothing to start in Docker."; \
	else \
		echo "$(COMPOSE) $$profiles up -d --wait"; \
		$(COMPOSE) $$profiles up -d --wait --wait-timeout 300 || { \
			echo ""; echo "Not all containers are healthy. See: make logs"; \
			echo "If Langfuse does not start, set LANGFUSE=off (or hosted) in .env and run make up again."; exit 1; }; \
		echo "Up ($$profiles). Next: make setup"; \
	fi

down:
	$(COMPOSE) --profile n8n --profile langfuse down

setup: .env
	uv run kit setup $(if $(FORCE),--force)

# UC only if you give it (make n8n-export UC=travel); else all use cases.
n8n-export: .env
	uv run kit n8n-export $(if $(filter command line,$(origin UC)),$(UC))

check: .env
	uv run kit check

serve: .env
	uv run kit serve

eval: .env
	uv run kit eval $(UC) $(ENGINE) $(if $(CASES),--cases $(CASES)) $(if $(NAME),--name $(NAME)) $(if $(HOLDOUT),--holdout)

run-case: .env
	uv run kit run-case $(UC) $(ENGINE) $(CASE)

logs:
	$(COMPOSE) --profile n8n --profile langfuse logs -f --tail 100

reset:
	@printf "This deletes the n8n workflows, executions and all Langfuse traces of the kit. Type yes to go on: "; \
	read answer; [ "$$answer" = "yes" ] || { echo "Stopped. Nothing deleted."; exit 1; }
	$(COMPOSE) --profile n8n --profile langfuse down -v
	@echo "Deleted. N8N_API_KEY and N8N_OPENAI_CREDENTIAL_ID in .env are now stale: make setup replaces them."

test:
	uv run pytest -q

# Settings in pyproject.toml ([tool.ruff]); data and run files are excluded there.
fmt:
	uv run ruff check --select I --fix --quiet .
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .
