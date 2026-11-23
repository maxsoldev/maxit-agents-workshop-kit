---
name: n8n-workflow
description: Change the kit's n8n agent (engine n8n) through its files. Use to edit an n8n prompt, add, rename or rewire a node in usecases/<uc>/n8n/workflow.json, sync with the n8n editor (n8n-push, n8n-pull, a sync STOP), or find which n8n node went wrong in a run.
---

# The n8n agent as files

The n8n agent of a use case lives in `usecases/<uc>/n8n/`:

- `workflow.json`: the workflow `Kit: <uc> workflow` (engine `n8n`, webhook path `<uc>`). A chain of nodes:
  code decides the path, the model fills in the steps.
- `prompts/<name>.md`: the prompts. A prompt field in the JSON holds only `={{PROMPT:<name>}}`.
  `<name>` = the node name in snake case: node `Read message` → `read_message.md` (its instructions,
  the system message) and `read_message.input.md` (what the node gets: the case and the earlier node
  outputs, as n8n expressions `{{ $('Node name').item.json... }}`).

n8n itself keeps its own copy in its database. The kit syncs the two:

| Command | Direction |
|---|---|
| `make n8n-push UC=<uc>` | files → n8n: fills `{{PROMPT:…}}`, `{{KIT_MODEL}}`, `{{KIT_TOOLS_URL}}`, `{{OPENAI_CREDENTIAL_ID}}`, updates and publishes |
| `make n8n-pull UC=<uc>` | n8n → files: the **published** version, placeholders back. No change = no diff |
| `make eval … ENGINE=n8n` | pulls first if only n8n changed ("n8n changes saved to …"), stops if the files changed and are not pushed |

The **conflict guard** remembers the hash of the last sync (`runs/n8n-imported.json`). When both sides
changed since then, push, pull and eval stop with `STOP` / "Nothing was overwritten" and name both
commands. Read which side has the edits you want, then run exactly one of them with `FORCE=1` (the other
side goes to `runs/n8n-backup/` first). Ask the person when you cannot tell which edits matter.

## Change a prompt

1. Edit `prompts/<name>.md`. Keep the n8n expressions (`{{ $(...) }}`) as they are, unless the change is
   about what the node gets.
2. `make n8n-push UC=<uc>`. Done when it prints `OK    Kit: <uc> workflow: updated from the files, active`.
3. `make eval UC=<uc> ENGINE=n8n NAME=<short-name>` (then read the run with the `eval-loop` skill).

The person may also edit in the n8n editor (http://localhost:5702 or `N8N_URL`) and click **Publish**. Then
`make n8n-pull UC=<uc>` (or the next eval) brings it into the files; read `git diff` before you edit more.

## Change the graph (`workflow.json`)

The starters have one AI step. Marketing: `Case in` → `Read the rules` (tool `rules_get`, all of
`n8n/rules/`) → `Triage` (AI, model `Triage model`) → `Read result` (code: JSON text → `output`) →
`Result out`. Travel: the same with `Read message` (AI, model `Read model`). Notes (`Note: …`) are sticky notes.

Rules for an edit by hand:

- **Connections are keyed by node name**, not id. A rename changes the key in `connections`, every
  `"node": "<name>"` that points to it, every `$('<name>')` in the code nodes and in `prompts/*.input.md`,
  and the prompt file names (`git mv prompts/old.md prompts/new.md`, then the placeholder).
- A new node needs a unique `id` (a new UUID), a `position` `[x, y]` (main steps go left to right at y = 0,
  260–300 apart in x; a model sub-node sits at y = 300 under its AI node; move the later nodes right), and
  `type` and `typeVersion` copied from a node of the same kind.
- A new AI step: copy `Triage` (or `Read message`) + its model node + a parse code node (`Read result`), and connect the
  model with `ai_languageModel`. Its prompts go in two new files with placeholders.
- A tool step is an HTTP Request node: `POST {{KIT_TOOLS_URL}}/call/<tool>`, body "Using Fields Below", one
  field per tool argument (copy `Read the rules`). `uv run kit tool` lists the tools.
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
