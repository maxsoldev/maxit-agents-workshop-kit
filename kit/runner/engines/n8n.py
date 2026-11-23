"""Engine `n8n`: POST the case to the n8n webhook; the response body is the result (contracts §4).

After the answer, the runner reads the execution (found by our `x-kit-call` header, needs N8N_API_KEY) for the
link, the tool calls, the node spans of the trace, and where a failed run broke. Before a run: the schema must be
OpenAI-strict, and the n8n workflow and the files must be in sync (kit/setup.py owns the sync).
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

from kit import n8n_files
from kit.config import settings
from kit.runner.engines import n8n_execution
from kit.runner.engines.common import TIMEOUTS, Answer, as_result

__all__ = ["call", "warning", "before_run", "version_hash"]


def call(uc: str, engine_input: dict, extra_env: dict | None = None) -> Answer:
    """POST the case to the webhook `<uc>`. meta["execution_url"] links to the n8n execution (if found);
    meta["tool_calls"] and meta["n8n_nodes"] (for the trace, not the run file) come from its data."""
    path, call_id = uc, uuid.uuid4().hex
    output, error, meta = post(path, engine_input, call_id)
    if not meta.get("status"):  # n8n did not answer, so there is no execution
        return output, error, meta
    try:
        meta["execution_url"], execution = find_execution(path, call_id)
        meta["tool_calls"] = n8n_execution.tool_calls(execution)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        meta["execution_url"] = None  # a link is nice to have; never fail the case for it
        execution = None
    if error and (failure := n8n_execution.failure(execution)):  # n8n only says "Error in workflow"
        error = f"{failure} (open the n8n execution: {meta['execution_url']})"
    try:
        meta["n8n_nodes"] = n8n_execution.nodes(execution)
    except Exception:
        pass  # the node spans are nice to have; never fail the case for them
    return output, error, meta


def post(path: str, engine_input: dict, call_id: str) -> Answer:
    """POST to /webhook/<path>. Every way it can go wrong becomes an error text that says what to do."""
    s = settings()
    url = f"{s.n8n_url}/webhook/{path}"
    meta = {"url": url}
    timeout = TIMEOUTS["n8n"]
    try:
        # n8n sends nothing until the workflow ends, so the read timeout is the total time.
        # call_with_deadline adds a hard wall-clock limit on top.
        r = httpx.post(
            url, json=engine_input, headers={"x-kit-call": call_id}, timeout=httpx.Timeout(timeout, connect=10)
        )
    except httpx.ConnectError:
        return None, f"cannot reach n8n at {s.n8n_url}: is it running? run `make up` (or check N8N_URL in .env)", meta
    except httpx.TimeoutException:
        return None, f"n8n did not answer in {timeout} s: look at the execution in n8n (Executions)", meta
    meta["status"] = r.status_code
    if r.status_code == 404:
        why = f"is the workflow active and is its webhook path '{path}'? run `make setup`"
        return None, f"n8n answered 404 for /webhook/{path}: {why}", meta
    if r.status_code >= 400:
        return None, f"n8n answered {r.status_code}: {r.text[:300]} (look at Executions in n8n)", meta
    try:
        value = r.json()
    except ValueError:
        text = f"n8n answered text, not JSON: {r.text[:200]!r}. End the workflow with Respond to Webhook (JSON)"
        return None, text, meta
    output, error = as_result(value)
    return output, error, meta


def _client():
    from kit import setup  # setup owns the n8n API client

    return setup.N8n.from_settings(timeout=10)


def active_workflow(n8n, path: str) -> dict | None:
    """The active workflow with the webhook path `path` (the last one, if there are more)."""
    from kit import setup

    hits = [w for w in n8n.workflows() if w.get("active") and path in setup.webhook_paths(w)]
    return hits[-1] if hits else None


def find_execution(path: str, call_id: str) -> tuple[str | None, dict | None]:
    """Find our execution: the newest ones of the workflow, matched by our `x-kit-call` header.
    Return (its URL, the execution with data). Falls back to the workflow's Executions list and None."""
    s = settings()
    if not s.n8n_api_key:
        return None, None
    n8n = _client()
    w = active_workflow(n8n, path)
    if not w:
        return None, None
    wid = w["id"]
    for _attempt in range(3):  # n8n saves the execution just after it answers
        r = n8n.api("GET", f"/executions?workflowId={wid}&limit=10&includeData=true")
        for e in r.json().get("data", []):
            if n8n_execution.has_call(e, call_id):
                return f"{s.n8n_url}/workflow/{wid}/executions/{e['id']}", e
        time.sleep(1)
    return f"{s.n8n_url}/workflow/{wid}/executions", None


def warning(uc: str) -> str | None:
    """A warning about the n8n workflow of `uc`, else None: the files and n8n are not in sync
    (kit/setup.py `sync_advice`), or n8n has edits that are not published (the webhook, so the eval,
    runs the published version)."""
    from kit import config, setup  # setup owns the sync state

    st = setup.n8n_status(uc)
    if not st:
        return None
    rel = st["path"].relative_to(config.ROOT) if st["path"].is_relative_to(config.ROOT) else st["path"]
    found = []
    if st["state"] == "n8n":
        found.append(f"'{st['name']}' has n8n edits that are not in {rel} yet: the eval saves them to the files.")
    elif st["state"] != "same":
        found.append(setup.sync_advice(st["state"], uc, rel, st["name"]))
    if st["draft"]:
        found.append(setup.draft_advice(uc, st["name"]))
    return " ".join(found) or None


def strict_problem(node, where: str = "the result") -> str | None:
    """Why a JSON Schema is not OpenAI-strict (the n8n model nodes send it as strict json_schema), or None.
    Strict: every object has all its properties in `required` and `additionalProperties: false`."""
    if isinstance(node, list):
        return next((p for x in node if (p := strict_problem(x, where))), None)
    if not isinstance(node, dict):
        return None
    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties") or {}
        missing = [k for k in props if k not in (node.get("required") or [])]
        if missing:
            return f"{where}: {', '.join(missing)} not in required"
        if node.get("additionalProperties") is not False:
            return f"{where}: no additionalProperties: false"
        return next(
            (p for k, v in props.items() if (p := strict_problem(v, k if where == "the result" else f"{where}.{k}"))),
            None,
        )
    return next((p for v in node.values() if (p := strict_problem(v, where))), None)  # items, anyOf, $defs


def before_run(uc: str, schema: dict) -> dict:
    """Checks before an n8n eval; returns what goes into the run file (`n8n_sync`, `warning`).
    Raises CaseError (schema not strict) or ValueError (the files changed: run make n8n-push)."""
    from kit import setup  # n8n -> files if only n8n changed; stops if the files changed (contracts §5)
    from kit.runner.cases import CaseError

    if problem := strict_problem(schema):
        raise CaseError(
            f"n8n needs a strict output.schema.json: {problem}. Put every field in required, and "
            'additionalProperties: false in every object; use "" (or []) for an empty value'
        )
    extra = {}
    if synced := setup.sync_before_eval(uc):
        extra["n8n_sync"] = synced
        print(f"NOTE  {synced}", file=sys.stderr, flush=True)
    if found := warning(uc):
        extra["warning"] = found  # e.g. edits in n8n that are not published: the run used the published one
        print(f"WARN  {found}", file=sys.stderr, flush=True)
    return extra


def version_hash(uc: str) -> str | None:
    """The hash of the workflow with its prompts (the same as the sync state), or None."""
    from kit import setup

    return setup.files_hash(n8n_files.workflow_file(uc))
