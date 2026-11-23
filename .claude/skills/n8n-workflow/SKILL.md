---
name: n8n-workflow
description: Change the kit's n8n agent (engine n8n) through its files. Use to change an n8n step's business rules file, add, rename or rewire a node in usecases/<uc>/n8n/workflow.json, sync with the n8n editor (n8n-push, n8n-pull, a sync STOP), or find which n8n node went wrong in a run.
---

# The n8n agent as files

The n8n agent of a use case lives in `usecases/<uc>/n8n/`:

- `workflow.json`: the workflow `Kit: <uc> workflow` (engine `n8n`, webhook path `<uc>`). A chain of nodes:
  code decides the path, the model fills in the steps.
- `<step>.md` (e.g. `triage.md`): the business rules file of an AI step, its prompt as plain text. The
  node **Read the rules** gets it on every case (`GET {{KIT_TOOLS_URL}}/rules/<uc>/n8n/<step>.md`); the AI
  node's first chat message is `={{ $('Read the rules').item.json.text }}`. Its `text` (what the node gets,
  e.g. `Email: {{ $('Case in').item.json.body.input.email }}`) is wiring, in `workflow.json`.

n8n itself keeps its own copy of the graph in its database. The kit syncs the two (the `.md` files need no sync):

| Command | Direction |
|---|---|
| `make n8n-push UC=<uc>` | workflow.json → n8n: fills `{{KIT_MODEL}}`, `{{KIT_TOOLS_URL}}`, `{{OPENAI_CREDENTIAL_ID}}`, updates and publishes |
| `make n8n-pull UC=<uc>` | n8n → files: the **published** version, placeholders back. No change = no diff |
| `make eval … ENGINE=n8n` | pulls first if only n8n changed ("n8n changes saved to …"), stops if the files changed and are not pushed |

The **conflict guard** remembers the hash of the last sync (`runs/n8n-imported.json`). When both sides
changed since then, push, pull and eval stop with `STOP` / "both changed" and name both
commands. Read which side has the edits you want, then run exactly one of them with `FORCE=1` (the other
side goes to `runs/n8n-backup/` first). Ask the person when you cannot tell which edits matter.

## Change a prompt (a business rules file)

1. Edit `n8n/<step>.md` (or the person edits it on the kit's Rules page). Plain text, no n8n expressions.
2. Nothing to push: the next case reads the file.
3. `make eval UC=<uc> ENGINE=n8n NAME=<short-name>` (then read the run with the `eval-loop` skill).

## Change the graph (`workflow.json`)

The starters have one AI step. Marketing: `Case in` → `Read the rules` (GETs `n8n/triage.md`) → `Triage` (AI, model `Triage model`) → `Read result` (code: JSON text → `output`) →
`Result out`. Travel: the same with `Read message` (AI, model `Read model`). Notes (`Note: …`) are sticky notes.

Rules for an edit by hand:

- **Connections are keyed by node name**, not id. A rename changes the key in `connections`, every
  `"node": "<name>"` that points to it, and every `$('<name>')` in the code nodes and the node texts.
- A new node needs a unique `id` (a new UUID), a `position` `[x, y]` (main steps go left to right at y = 0,
  260–300 apart in x; a model sub-node sits at y = 300 under its AI node; move the later nodes right), and
  `type` and `typeVersion` copied from a node of the same kind.
- A new AI step: copy `Triage` (or `Read message`) + its model node + a parse code node (`Read result`), and connect the
  model with `ai_languageModel`. Give it its own business rules file `n8n/<step>.md`: copy `Read the rules`
  (a new name, e.g. `Read the draft rules`), change the file in its URL, and point the AI node's first chat
  message to that node. The Rules page shows the new file at once.
- A tool step is an HTTP Request node: `POST {{KIT_TOOLS_URL}}/call/<tool>`, body "Using Fields Below", one
  field per tool argument.
  (Not like `Read the rules`: that one is a plain GET of a file.) `uv run kit tool` lists the tools.
- The last AI step gets the result shape from the case: Response Format → JSON Schema, by expression from
  `$('Case in').item.json.body.output_schema`. n8n sends it as strict JSON Schema, so `output.schema.json`
  must list every field in `required` and have `additionalProperties: false` (the eval checks it first).
- Put the result in `Result out` (Respond to Webhook, JSON). Keep `pinData` out of the files.

Then `make n8n-push UC=<uc>`: an error there names the node. Then the eval.

## Find the node that went wrong

- In the run file (`runs/<run_id>.json`), a case with `error: "n8n failed at <node>: <message>"` names the node;
  `n8n_execution_url` opens that execution in n8n (every node's input and output).
- A wrong answer without an error: `meta.tool_calls` shows what each tool step got. Open the case's
  `trace_url` (Langfuse): one line per node in run order; the model line under an AI node has its full prompt
  and answer. That shows whether the prompt or the input of the node is the problem.
- `make run-case UC=<uc> ENGINE=n8n CASE=<id>` prints input, output, tool calls, checks and the execution link
  for one case.

## Keys

The files hold placeholders only: the OpenAI key is in the n8n credential, made by `make setup` from `.env`.
Write `{{OPENAI_CREDENTIAL_ID}}`, `{{KIT_MODEL}}` and `{{KIT_TOOLS_URL}}`, never their values, and never read
or print `.env`.
