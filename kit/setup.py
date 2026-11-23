"""`kit setup` and `kit check`.

setup: checks the host tools, sets up n8n (owner, API key, OpenAI credential, workflows) and checks Langfuse.
check: runs the first case of each use case on each engine and prints ANSWERS (with the check score) or
ERROR (and what to fix).
Both are safe to run again. They print names of keys, never their values.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

from kit import n8n_files
from kit.config import N8N_ENGINES, ROOT, RUNS_DIR, USECASES_DIR, parse_engines, settings, usecases

ENV_FILE = ROOT / ".env"
OPENAI_CRED_NAME = "Kit OpenAI"
LANGFUSE_WAIT_S = 180
N8N_WAIT_S = 120


# ---------- pure helpers (tested in tests/test_setup.py) ----------


def read_env(path: Path) -> dict[str, str]:
    """KEY=value lines of an env file. Skips blank lines and comments; strips quotes."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return out


def set_env_key(path: Path, key: str, value: str) -> None:
    """Set KEY=value in the env file: replace the first line of that key (drop later ones), else append."""
    lines = path.read_text().splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
    os.environ[key] = value  # so settings() in this process sees it too


def compose_profiles(env: dict[str, str]) -> list[str]:
    """Compose profiles to start: n8n if N8N=local (and n8n is in KIT_ENGINES), langfuse if LANGFUSE=local.
    Both default to local."""
    out = []
    if env.get("N8N", "local").strip().lower() == "local" and "n8n" in parse_engines(env.get("KIT_ENGINES", "")):
        out.append("n8n")
    if env.get("LANGFUSE", "local").strip().lower() == "local":
        out.append("langfuse")
    return out


def service_notes(env: dict[str, str]) -> list[str]:
    """One line for each service that `make up` does not start, and why."""
    notes = []
    n8n_mode = env.get("N8N", "local").strip().lower()
    if "n8n" not in parse_engines(env.get("KIT_ENGINES", "")):
        notes.append(f"n8n: not used (KIT_ENGINES={env.get('KIT_ENGINES', '').strip()}), not started")
    elif n8n_mode != "local":
        notes.append(f"n8n: N8N={n8n_mode} in .env, not started here")
    lf_mode = env.get("LANGFUSE", "local").strip().lower()
    if lf_mode == "off":
        notes.append("Langfuse: LANGFUSE=off in .env, no traces")
    elif lf_mode != "local":
        notes.append(f"Langfuse: LANGFUSE={lf_mode} in .env, not started here")
    return notes


def fill_placeholders(text: str, credential_id: str, tools_url: str) -> str:
    """Put the real values into a workflow.json text."""
    return (
        text.replace("{{OPENAI_CREDENTIAL_ID}}", credential_id)
        .replace("{{KIT_TOOLS_URL}}", tools_url)
        .replace("{{KIT_MODEL}}", settings().model)
    )


def workflow_body(wf: dict) -> dict:
    """Only the fields the n8n public API accepts on create and update. No pinData: the files define the
    pipeline, not one case (Debug in editor pins every node; an export must not save that). To see the data
    of a case, open its n8n execution."""
    return {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf.get("connections", {}),
        "settings": wf.get("settings") or {},
    }


def workflow_hash(wf: dict) -> str:
    """Hash of what a workflow does: nodes (name, type, version, parameters, credential id) and links.
    Node positions do not count."""
    nodes = []
    for n in wf.get("nodes", []):
        d = {k: n.get(k) for k in ("name", "type", "typeVersion", "parameters")}
        # n8n stores its own credential name; only the id matters.
        d["credentials"] = {t: (c or {}).get("id") for t, c in (n.get("credentials") or {}).items()}
        nodes.append(json.dumps(d, sort_keys=True))
    text = json.dumps([sorted(nodes), wf.get("connections", {})], sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def same_workflow(a: dict, b: dict) -> bool:
    """True if two workflows have the same nodes (name, type, version, parameters, credentials) and links."""
    return workflow_hash(a) == workflow_hash(b)


def load_workflow(path: Path, credential_id: str, tools_url: str) -> dict:
    """A workflow file as n8n gets it: placeholders filled, prompts from n8n/prompts/*.md put in."""
    wf = json.loads(fill_placeholders(path.read_text(encoding="utf-8"), credential_id, tools_url))
    return n8n_files.fill_prompts(wf, path.parent / "prompts")


def files_hash(path: Path) -> str | None:
    """workflow_hash of a workflow file with its prompts, as n8n would get it. None if it cannot be read."""
    s = settings()
    try:
        return workflow_hash(load_workflow(path, s.n8n_openai_credential_id, s.tools_url_docker))
    except (OSError, ValueError, KeyError):
        return None


def published(full: dict) -> dict:
    """The version the webhook runs. n8n 2.x keeps editor edits as a draft; `activeVersion` is the published one."""
    av = full.get("activeVersion")
    if full.get("active") and isinstance(av, dict) and av.get("nodes") is not None:
        return {**full, "nodes": av["nodes"], "connections": av.get("connections") or {}}
    return full


def has_draft(full: dict) -> bool:
    """True if n8n has editor edits that are not published."""
    return bool(full.get("activeVersionId")) and workflow_hash(full) != workflow_hash(published(full))


def sync_state(files: str | None, n8n: str, last: str | None) -> str:
    """Which side changed since the last sync (push or pull): same, n8n, files, both,
    or unknown (they differ and this laptop has no record of a sync)."""
    if files == n8n:
        return "same"
    if last is None:
        return "unknown"
    if files == last:
        return "n8n"
    return "files" if n8n == last else "both"


def sync_advice(state: str, uc: str, rel, name: str) -> str:
    """What to run for a sync state (contracts §5). Never overwrites by itself."""
    files = f"{rel} (or n8n/prompts/)"
    if state == "files":
        return f"{files} changed since the last sync; n8n still has the old '{name}'. Run: make n8n-push UC={uc}"
    if state == "n8n":
        return f"'{name}' has edits in n8n that are not in {rel}. Save them: make n8n-pull UC={uc}"
    keep = (
        f"Keep the n8n version: make n8n-pull UC={uc} FORCE=1 (your files go to runs/n8n-backup/ first). "
        f"Keep the files: make n8n-push UC={uc} FORCE=1 (the n8n copy goes to runs/n8n-backup/ first)."
    )
    if state == "both":
        return f"'{name}' changed in n8n and {files} changed too, since the last sync. Nothing was overwritten. {keep}"
    return f"'{name}' in n8n is not the same as {files}, and this laptop has no record of a sync. {keep}"


def draft_advice(uc: str, name: str) -> str:
    return (
        f"n8n: '{name}' has edits that are not published. The eval runs the published version: "
        f"Publish in n8n, or run make n8n-push UC={uc} to discard them."
    )


IMPORTED_FILE = RUNS_DIR / "n8n-imported.json"  # workflow name -> hash of the last sync (push, pull or setup)


def imported_hashes() -> dict:
    try:
        return json.loads(IMPORTED_FILE.read_text())
    except (OSError, ValueError):
        return {}


def remember_import(name: str, wf: dict) -> None:
    data = imported_hashes()
    data[name] = workflow_hash(wf)
    IMPORTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    IMPORTED_FILE.write_text(json.dumps(data, indent=1))


def placeholders_back(value, credential_id: str, tools_url: str, model: str):
    """The opposite of fill_placeholders, on parsed JSON: real values -> {{...}} placeholders."""
    if isinstance(value, dict):
        return {k: placeholders_back(v, credential_id, tools_url, model) for k, v in value.items()}
    if isinstance(value, list):
        return [placeholders_back(v, credential_id, tools_url, model) for v in value]
    if isinstance(value, str):
        if credential_id and value == credential_id:
            return "{{OPENAI_CREDENTIAL_ID}}"
        if model and value == model:
            return "{{KIT_MODEL}}"
        return value.replace(tools_url, "{{KIT_TOOLS_URL}}") if tools_url else value
    return value


# ---------- output ----------


def ok(msg: str) -> None:
    print(f"  OK    {msg}")


def warn(msg: str) -> None:
    print(f"  WARN  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, type(e).__name__
    text = (p.stdout or p.stderr).strip()
    return p.returncode == 0, text.splitlines()[0] if text else ""


# ---------- step 1: host checks ----------


def model_key_works() -> tuple[bool, str]:
    """One cheap call: GET {base}/models (OpenAI or the gateway). Returns (works, short text)."""
    s = settings()
    base = s.openai_base_url or "https://api.openai.com/v1"
    try:
        r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {s.openai_api_key}"}, timeout=15)
    except httpx.HTTPError as e:
        return False, f"cannot reach {base} ({type(e).__name__})"
    if r.status_code == 200:
        return True, f"{base} answers"
    try:
        err = r.json().get("error", r.text)
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
    except ValueError:
        msg = r.text
    return False, f"{base} answered {r.status_code}: {' '.join(msg.split())[:200].rstrip('. ')}"


def check_host() -> bool:
    print("Host")
    good = True
    if ENV_FILE.exists():
        ok(".env exists")
    else:
        fail(".env is missing. Run: cp .env.example .env  (then set OPENAI_API_KEY)")
        return False
    if settings().openai_api_key:
        works, text = model_key_works()
        if works:
            ok(f"OPENAI_API_KEY works ({text})")
        else:
            fail(
                f"OPENAI_API_KEY does not work: {text}. "
                "Fix the key (or OPENAI_BASE_URL) in .env, then run make setup again"
            )
            good = False
    else:
        fail("OPENAI_API_KEY is empty. Put your key in .env, then run make setup again")
        good = False
    if (settings().n8n_mode == "local" and "n8n" in settings().engines) or settings().langfuse_mode == "local":
        up, _ = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if up:
            ok("Docker is running")
        else:
            fail("Docker is not running. Start Docker Desktop, then run make up")
            good = False
    found, ver = _run(["uv", "--version"])
    if found:
        ok(f"uv: {ver}")
    else:
        fail("uv not found. Install it: https://docs.astral.sh/uv/")
    if "claude" in settings().engines:
        found, ver = _run(["claude", "--version"])
        if found:
            ok(f"claude: {ver}")
        else:
            warn("claude not found: the claude engine will not work. Install Claude Code and log in")
    return good


# ---------- step 2: n8n ----------


class N8n:
    """Small client for n8n: internal REST (/rest, session login) for the owner and API key,
    public API (/api/v1, X-N8N-API-KEY) for credentials and workflows."""

    def __init__(self, base: str):
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=30, headers={"browser-id": "kit-setup"})
        self.key = ""

    @classmethod
    def from_settings(cls, timeout: float = 30) -> N8n:
        """A client for N8N_URL with N8N_API_KEY from .env (it does not check that they work)."""
        s = settings()
        n8n = cls(s.n8n_url)
        n8n.key = s.n8n_api_key
        n8n.http.timeout = httpx.Timeout(timeout)
        return n8n

    def ready(self) -> bool:
        try:
            return self.http.get("/healthz/readiness").status_code == 200
        except httpx.HTTPError:
            return False

    def api(self, method: str, path: str, body=None) -> httpx.Response:
        return self.http.request(method, f"/api/v1{path}", json=body, headers={"X-N8N-API-KEY": self.key})

    def key_works(self, key: str) -> bool:
        if not key:
            return False
        r = self.http.get("/api/v1/workflows?limit=1", headers={"X-N8N-API-KEY": key})
        return r.status_code == 200

    def workflows(self) -> list[dict]:
        out, cursor = [], ""
        while True:
            r = self.api("GET", f"/workflows?limit=100{f'&cursor={cursor}' if cursor else ''}")
            r.raise_for_status()
            page = r.json()
            out += page.get("data", [])
            cursor = page.get("nextCursor") or ""
            if not cursor:
                return out


def wait_for(check, seconds: int) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(3)
    return False


def setup_n8n(force: bool = False) -> bool:
    s = settings()
    print(f"n8n ({s.n8n_mode}, {s.n8n_url})")
    n8n = N8n(s.n8n_url)
    if s.n8n_mode != "local":
        if not n8n.ready():
            fail(f"n8n does not answer at {s.n8n_url}. Check N8N_URL in .env")
            return False
        if not n8n.key_works(s.n8n_api_key):
            fail("n8n rejects N8N_API_KEY. Ask the trainer for your key and put it in .env")
            return False
        ok("URL and API key work")
        return True

    if not wait_for(n8n.ready, N8N_WAIT_S):
        fail(f"n8n does not answer at {s.n8n_url}. Run make up, then make logs to see why")
        return False
    if n8n.key_works(s.n8n_api_key):  # owner and key exist: no login needed (n8n limits logins)
        n8n.key = s.n8n_api_key
        ok("owner account and API key in .env work")
    else:
        if not s.n8n_owner_password:
            fail("N8N_OWNER_PASSWORD is empty in .env (8+ chars, 1 number, 1 capital letter)")
            return False
        r = n8n.http.post(
            "/rest/owner/setup",
            json={
                "email": s.n8n_owner_email,
                "firstName": "Kit",
                "lastName": "Owner",
                "password": s.n8n_owner_password,
            },
        )
        ok("owner account created" if r.status_code == 200 else "owner account exists")
        r = n8n.http.post(
            "/rest/login", json={"emailOrLdapLoginId": s.n8n_owner_email, "password": s.n8n_owner_password}
        )
        if r.status_code == 429:
            fail("n8n blocks logins for a short time (too many tries). Wait a minute, then run make setup again")
            return False
        if r.status_code != 200:
            fail(
                f"login as {s.n8n_owner_email} failed ({r.status_code}). Is N8N_OWNER_PASSWORD the one you used "
                "first? If you do not know it: make reset, then make up and make setup"
            )
            return False
        r = n8n.http.get("/rest/api-keys/scopes")
        scopes = r.json().get("data", []) if r.status_code == 200 else []
        r = n8n.http.post(
            "/rest/api-keys", json={"label": f"kit-{int(time.time())}", "expiresAt": None, "scopes": scopes}
        )
        data = (r.json().get("data") or {}) if r.status_code == 200 else {}
        new = data.get("rawApiKey") or data.get("apiKey") or ""
        if not n8n.key_works(new):
            fail(
                f"could not create an API key ({r.status_code}). Create one in n8n: Settings > n8n API, "
                "and put it in .env as N8N_API_KEY"
            )
            return False
        set_env_key(ENV_FILE, "N8N_API_KEY", new)
        n8n.key = new
        ok("API key created and written to .env (N8N_API_KEY)")

    cred_id = setup_credential(n8n)
    if not cred_id:  # import anyway, so you can look at the workflows; activate them later
        import_workflows(n8n, "")
        return False
    return import_workflows(n8n, cred_id, force)


def key_fingerprint(key: str) -> str:
    """Short hash of a key. n8n does not show a stored key, so .env keeps this to see if the key changed."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def setup_credential(n8n: N8n) -> str:
    """The OpenAI credential, made from OPENAI_API_KEY. Kept if it exists; its key is updated if it changed."""
    s = settings()
    if not s.openai_api_key:
        warn("no OpenAI credential yet: OPENAI_API_KEY is empty")
        return ""
    data = {"apiKey": s.openai_api_key, "url": s.openai_base_url_docker, "header": False}
    fingerprint = key_fingerprint(s.openai_api_key + "|" + data["url"])
    cred_id = s.n8n_openai_credential_id
    if cred_id and n8n.api("GET", f"/credentials/{cred_id}").status_code == 200:
        if read_env(ENV_FILE).get("N8N_OPENAI_KEY_FINGERPRINT") == fingerprint:
            ok(f"credential '{OPENAI_CRED_NAME}' exists")
            return cred_id
        r = n8n.api("PATCH", f"/credentials/{cred_id}", {"name": OPENAI_CRED_NAME, "type": "openAiApi", "data": data})
        if r.status_code < 300:
            set_env_key(ENV_FILE, "N8N_OPENAI_KEY_FINGERPRINT", fingerprint)
            ok(f"credential '{OPENAI_CRED_NAME}' updated with the key from .env")
            return cred_id
    r = n8n.api("POST", "/credentials", {"name": OPENAI_CRED_NAME, "type": "openAiApi", "data": data})
    if r.status_code >= 300:
        fail(f"could not create the OpenAI credential ({r.status_code}): {r.text[:200]}")
        return ""
    cred_id = r.json()["id"]
    set_env_key(ENV_FILE, "N8N_OPENAI_CREDENTIAL_ID", cred_id)
    set_env_key(ENV_FILE, "N8N_OPENAI_KEY_FINGERPRINT", fingerprint)
    ok(f"credential '{OPENAI_CRED_NAME}' created (N8N_OPENAI_CREDENTIAL_ID in .env)")
    return cred_id


def webhook_paths(wf: dict) -> set[str]:
    return {
        n.get("parameters", {}).get("path", "")
        for n in wf.get("nodes", [])
        if n.get("type") == "n8n-nodes-base.webhook"
    } - {""}


def free_webhook_paths(n8n: N8n, body: dict, wid: str) -> None:
    """n8n allows one active workflow per webhook path. Deactivate any other workflow on our path
    (an old copy, or one you renamed in n8n), and say so."""
    paths = webhook_paths(body)
    for w in n8n.workflows():
        if w["id"] != wid and w.get("active") and webhook_paths(w) & paths:
            n8n.api("POST", f"/workflows/{w['id']}/deactivate")
            warn(f"deactivated '{w['name']}': it used the same webhook path ({', '.join(sorted(paths))})")


def restart_n8n(n8n: N8n) -> bool:
    """n8n can keep an old webhook in memory after a deactivate (activation then fails with 409).
    A restart clears it. Local n8n only."""
    warn("n8n still holds an old webhook: restarting n8n once (about 20 s)")
    subprocess.run(["docker", "compose", "restart", "n8n"], cwd=ROOT, capture_output=True)
    for _ in range(60):
        if n8n.ready():
            return True
        time.sleep(1)
    return False


KEPT: list[str] = []  # workflows the last push kept as they are in n8n (the guard stopped it)


def import_workflows(n8n: N8n, cred_id: str, force: bool = False, only_uc: str = "") -> bool:
    """Push: create or update each usecases/*/n8n/*.json (workflow.json, agent.json, with the prompts from
    n8n/prompts/) by name, then activate (publish) it. If the published n8n copy changed since the last sync,
    keep it, unless `force`. Unpublished editor edits are discarded. The old n8n copy goes to runs/n8n-backup/."""
    files = sorted(USECASES_DIR.glob(f"{only_uc or '*'}/n8n/*.json"))
    if not files:
        warn(f"no usecases/{only_uc or '*'}/n8n/*.json to import")
        return not only_uc
    good = True
    by_name = {w["name"]: w for w in n8n.workflows()}
    for f in files:
        rel = f.relative_to(ROOT)
        try:
            body = workflow_body(load_workflow(f, cred_id, settings().tools_url_docker))
        except (ValueError, KeyError) as e:
            fail(f"{rel}: not a valid workflow ({e})")
            good = False
            continue
        old = by_name.get(body["name"])
        if not cred_id:  # no model key yet: import without the credential, do not activate
            if old is not None:
                warn(f"{body['name']}: kept as it is in n8n. Activate after the key is set: make setup")
                continue
            for node in body["nodes"]:
                node.pop("credentials", None)
            r = n8n.api("POST", "/workflows", body)
            if r.status_code >= 300:
                fail(f"{rel}: import failed ({r.status_code}): {r.text[:200]}")
                good = False
            else:
                warn(
                    f"{body['name']}: imported, not active (no OpenAI credential). Activate after the key "
                    "is set: put OPENAI_API_KEY in .env, then make setup"
                )
            continue
        if old is None:
            r = n8n.api("POST", "/workflows", body)
            if r.status_code >= 300:
                fail(f"{rel}: import failed ({r.status_code}): {r.text[:200]}")
                good = False
                continue
            wid, state = r.json()["id"], "imported"
            remember_import(body["name"], body)
        else:
            wid = old["id"]
            full = n8n.api("GET", f"/workflows/{wid}").json()
            uc_name = f.parent.parent.name
            pinned = bool(full.get("pinData"))
            sync = sync_state(workflow_hash(body), workflow_hash(published(full)), imported_hashes().get(body["name"]))
            draft = has_draft(full)
            if sync == "same" and not pinned and not draft:
                state = "unchanged"
                remember_import(body["name"], body)
            elif sync in ("n8n", "both", "unknown") and not force:
                # the published n8n copy changed since the last sync (an edit in the editor): keep it
                warn(f"{body['name']}: kept the n8n version. {sync_advice(sync, uc_name, rel, body['name'])}")
                KEPT.append(body["name"])
                if full.get("active"):
                    continue
                state = "kept (n8n edits)"
            else:
                backup = RUNS_DIR / "n8n-backup" / f"{uc_name}-{f.stem}-{datetime.now():%Y%m%d-%H%M%S}.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(json.dumps(full, indent=2))
                if full.get("active"):
                    n8n.api("POST", f"/workflows/{wid}/deactivate")
                # pinData {} removes the pins in n8n; without the key n8n keeps them.
                r = n8n.api("PUT", f"/workflows/{wid}", {**body, "pinData": {}})
                if r.status_code >= 300:
                    fail(f"{rel}: update failed ({r.status_code}): {r.text[:200]}")
                    good = False
                    continue
                remember_import(body["name"], body)
                what = (
                    "updated from the files"
                    if sync != "same"
                    else "unpublished n8n edits discarded"
                    if draft
                    else "pinned data removed"
                )
                state = f"{what} (old copy: {backup.relative_to(ROOT)})"
            if full.get("active") and state == "unchanged":
                ok(f"{body['name']}: {state}, active")
                continue
        free_webhook_paths(n8n, body, wid)
        r = n8n.api("POST", f"/workflows/{wid}/activate")
        if r.status_code == 409 and settings().n8n_mode == "local" and restart_n8n(n8n):
            r = n8n.api("POST", f"/workflows/{wid}/activate")
        if r.status_code >= 300:
            fail(f"{body['name']}: {state}, but activation failed ({r.status_code}): {r.text[:200]}")
            good = False
        else:
            ok(f"{body['name']}: {state}, active")
    return good


# ---------- step 3: Langfuse ----------


def setup_langfuse() -> bool:
    s = settings()
    print(f"Langfuse ({s.langfuse_mode}, {s.langfuse_host})")
    if s.langfuse_mode == "off":
        ok("off: no traces. The runner and the evals work the same")
        return True
    switch = (
        "To go on without local Langfuse, set in .env: LANGFUSE=off (no traces), or LANGFUSE=hosted with "
        "LANGFUSE_HOST and the keys from the trainer. Then run make up and make setup again"
    )

    def healthy() -> bool:
        try:
            return httpx.get(f"{s.langfuse_host}/api/public/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    if not wait_for(healthy, LANGFUSE_WAIT_S if s.langfuse_mode == "local" else 10):
        fail(f"Langfuse is not healthy at {s.langfuse_host}. See make logs. {switch}")
        return False
    try:
        r = httpx.get(
            f"{s.langfuse_host}/api/public/projects", timeout=10, auth=(s.langfuse_public_key, s.langfuse_secret_key)
        )
    except httpx.HTTPError as e:
        fail(f"Langfuse key check failed: {type(e).__name__}")
        return False
    if r.status_code != 200:
        fail(f"Langfuse rejects LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY ({r.status_code}). {switch}")
        return False
    names = ", ".join(p.get("name", "?") for p in r.json().get("data", []))
    ok(f"keys work (project: {names})")
    return True


def summary() -> None:
    s = settings()
    print("\nWhere things are")
    print(f"  Kit UI     http://localhost:{s.ui_port}   (start it: make serve)")
    print(f"  Tools MCP  {s.tools_url}   (n8n uses {s.tools_url_docker})")
    if "n8n" not in s.engines:
        print("  n8n        not used (KIT_ENGINES)")
    elif s.n8n_mode == "local":
        print(f"  n8n        {s.n8n_url}   login {s.n8n_owner_email}, password N8N_OWNER_PASSWORD in .env")
    else:
        print(f"  n8n        {s.n8n_url}   (hosted)")
    if s.langfuse_mode == "local":
        print(
            f"  Langfuse   {s.langfuse_host}   login {s.langfuse_user_email}, "
            "password LANGFUSE_INIT_USER_PASSWORD in .env"
        )
    elif s.langfuse_mode == "hosted":
        print(f"  Langfuse   {s.langfuse_host}   (hosted)")
    else:
        print("  Langfuse   off")


def setup(force: bool = False) -> int:
    host = check_host()
    if not ENV_FILE.exists():
        return 1
    print(f"Engines: {', '.join(settings().engines)} (KIT_ENGINES in .env)")
    if "n8n" in settings().engines:
        n8n = setup_n8n(force)
    else:
        print("n8n\n  SKIP  not in KIT_ENGINES")
        n8n = True
    lf = setup_langfuse()
    summary()
    if host and n8n and lf:
        print("\nSetup done. Next: make serve (keep it running), then make check in a second terminal.")
        return 0
    print("\nSetup is not complete: fix the FAIL lines above, then run make setup again.")
    return 1


# ---------- kit check ----------

# Line labels. ANSWERS: the engine gave a result (checks may fail). ERROR: no result; fix the setup.
ANSWERS, ERROR, SKIP = "ANSWERS  ", "ERROR    ", "SKIP     "

NEXT_STEP = {
    "n8n": "is n8n up (make up) and the workflow active (make setup)? Is the tools server on (make serve)?",
    "n8n-agent": "is n8n up (make up) and the agent example active (make setup)? Is the tools server on (make serve)?",
    "claude": "run `claude` once and log in; is the tools server on (make serve)?",
    "python": "make run-case UC={uc} ENGINE=python CASE={case} shows the agent log",
}
# Python failures: the OpenAI error in the message says what is wrong. First match wins.
PYTHON_HINTS = [
    (("AuthenticationError", "Error code: 401"), "check OPENAI_API_KEY in .env (make setup tests the key)"),
    (
        ("APIConnectionError", "APITimeoutError", "Connection error"),
        "the model API does not answer: check OPENAI_API_KEY and OPENAI_BASE_URL in .env, and your network",
    ),
    (("RateLimitError", "Error code: 429"), "rate limit or no credit: wait a minute, or check the credit of your key"),
    (
        ("BadRequestError", "PermissionDeniedError", "NotFoundError", "Error code: 4"),
        "the model API refused the call: check KIT_MODEL (the model name; your key or gateway must have it) "
        "and the API call in python/agent.py",
    ),
]


def next_step(engine: str, reason: str, uc: str = "<uc>", case: str = "<id>") -> str:
    """The next step after a failure; for python it fits the OpenAI error in `reason`."""
    if engine == "python":
        for needles, hint in PYTHON_HINTS:
            if any(n in reason for n in needles):
                return hint
    return NEXT_STEP[engine].format(uc=uc, case=case)


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def check() -> int:
    try:
        from kit.runner import run_eval
    except ImportError:
        print("SKIP  the runner (kit/runner) is not there yet: nothing to check.")
        return 0
    s = settings()
    if _port_open(s.tools_port):
        print(f"Using the tools server that already runs on :{s.tools_port}.")
    else:
        from kit import tools

        try:
            tools.start_background()
            print(f"Started the tools server on :{s.tools_port} for this check (it stops when the check ends).")
        except RuntimeError:
            if _port_open(s.tools_port):  # another process (make serve) took the port a moment ago
                print(f"Using the tools server that already runs on :{s.tools_port}.")
            else:
                print(f"WARN  the tools server did not start on :{s.tools_port}; tool calls will fail. Run make serve")
    unused = [e for e in ("n8n", "claude", "python") if e not in s.engines]
    if unused:
        print(f"{SKIP}{', '.join(unused)}: not in KIT_ENGINES")
    n8n_warning = getattr(sys.modules["kit.runner"], "n8n_warning", lambda uc, engine: None)
    for uc in usecases():  # e.g. after git pull: the kit has a newer workflow than n8n
        for engine in (e for e in s.engines if e in N8N_ENGINES):
            if warning := n8n_warning(uc, engine):
                print(f"WARN     {uc}/{engine}: {warning}")
    # One row per use case × engine, in print order: (uc, engine, first case id, a line known before any run).
    rows = []
    for uc in usecases():
        lines = (USECASES_DIR / uc / "cases.jsonl").read_text().splitlines()
        first = next((json.loads(x)["id"] for x in lines if x.strip()), None)
        if not first:
            rows.append((uc, None, None, f"{SKIP}{uc}: cases.jsonl has no cases"))
            continue
        for engine in s.engines:
            rows.append((uc, engine, first, _check_before_run(uc, engine, first, s)))
    todo = [r for r in rows if r[3] is None]
    if todo:
        print(f"Running {len(todo)} checks in parallel (one case each; this can take a few minutes) ...", flush=True)
    bad = good = 0
    with ThreadPoolExecutor(max_workers=max(1, len(todo))) as pool:
        futures = [None if line else pool.submit(_check_run, run_eval, uc, e, first) for uc, e, first, line in rows]
        for (_uc, _e, _first, line), future in zip(rows, futures, strict=True):
            line = line or future.result()  # wait for this row only: the lines stay in the fixed order
            bad += line.startswith(ERROR)
            good += line.startswith(ANSWERS)
            print(line, flush=True)
    if bad:
        print(f"\n{bad} engine(s) do not answer (ERROR lines). Fix them, then run make check again.")
        return 1
    print(f"\nAll {good} engines answer. Wrong answers are fine here: making them right is the day (docs/tutorial.md).")
    return 0


def _check_before_run(uc: str, engine: str, first: str, s) -> str | None:
    """The SKIP / ERROR line for a pair that cannot run; None if it can run."""
    label = f"{uc}/{engine} ({first})"
    part = "n8n/agent.json" if engine == "n8n-agent" else engine
    if not (USECASES_DIR / uc / part).exists():
        return f"{SKIP}{label}: no usecases/{uc}/{part}"
    n8n = engine in N8N_ENGINES
    if n8n and not s.n8n_api_key and s.n8n_mode == "local":
        return f"{ERROR}{label}: n8n is not set up. Next: make up, make setup"
    if n8n and not s.openai_api_key and s.n8n_mode == "local":
        return (
            f"{ERROR}{label}: no OPENAI_API_KEY, so the workflow is not active. "
            "Next: put the key in .env, then make setup"
        )
    return None


def _check_run(run_eval, uc: str, engine: str, first: str) -> str:
    """Run the first case of `uc` on `engine` (a thread of check); return its OK / FAIL line."""
    label = f"{uc}/{engine} ({first})"
    try:
        case = run_eval(uc, engine, case_ids=[first], name="check")["cases"][0]
    except Exception as e:  # the check must go on to the next engine
        return f"{ERROR}{label}: {type(e).__name__}: {e}. Next: {next_step(engine, str(e), uc, first)}"
    if case.get("error"):
        reason = case["error"]
        return f"{ERROR}{label}: {reason}. Next: {next_step(engine, reason, uc, first)}"
    # The engine answered. Failed checks are normal for a scaffold: making them pass is the day.
    checks = [c for c in case.get("checks", []) if c.get("passed") is not None]
    failed = [c for c in checks if c.get("passed") is False]
    if not failed:
        return f"{ANSWERS}{label}: all checks pass"
    reason = "; ".join(f"{c['type']} {c.get('field', '')}: {c.get('reason', '')}" for c in failed)
    return f"{ANSWERS}{label}: {len(checks) - len(failed)}/{len(checks)} checks pass. {reason[:200]}"


# ---------- kit n8n-export ----------


def same_order(new, old):
    """`new` with its keys in the order of `old` (nodes matched by name), so git diff shows only real changes."""
    if isinstance(new, dict) and isinstance(old, dict):
        keys = [k for k in old if k in new] + [k for k in new if k not in old]
        return {k: same_order(new[k], old.get(k)) for k in keys}
    if isinstance(new, list) and isinstance(old, list):
        by_name = {o.get("name"): o for o in old if isinstance(o, dict)}
        return [same_order(n, by_name.get(n.get("name")) if isinstance(n, dict) else None) for n in new]
    return new


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def save_workflow_files(path: Path, wf: dict) -> list[Path]:
    """Write an n8n workflow to `path` and its prompts to n8n/prompts/*.md, with the placeholders back
    ({{PROMPT:...}}, {{OPENAI_CREDENTIAL_ID}}, {{KIT_TOOLS_URL}}, {{KIT_MODEL}}). Same content = same bytes.
    Returns the files that changed."""
    s = settings()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    body, prompts = n8n_files.split_prompts(json.loads(json.dumps(workflow_body(wf))), path.stem)
    body = same_order(placeholders_back(body, s.n8n_openai_credential_id, s.tools_url_docker, s.model), old)
    changed = [path] if _write_if_changed(path, json.dumps(body, indent=2, ensure_ascii=False) + "\n") else []
    for name, text in prompts.items():
        p = path.parent / "prompts" / f"{name}.md"
        if _write_if_changed(p, text + "\n"):
            changed.append(p)
    return changed


def unused_prompts(folder: Path) -> list[Path]:
    """Prompt files in n8n/prompts/ that no workflow file of the folder points to (a node was renamed)."""
    used = set()
    for f in folder.glob("*.json"):
        try:
            used |= n8n_files.placeholder_names(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return [p for p in sorted((folder / "prompts").glob("*.md")) if p.stem not in used]


def backup_files(uc: str, path: Path) -> Path:
    """Copy a workflow file and its prompt files to runs/n8n-backup/ (before a pull overwrites them)."""
    dest = RUNS_DIR / "n8n-backup" / f"{uc}-{path.stem}-files-{datetime.now():%Y%m%d-%H%M%S}"
    for p in [path, *sorted((path.parent / "prompts").glob("*.md"))]:
        if p.exists():
            (dest / p.relative_to(path.parent)).parent.mkdir(parents=True, exist_ok=True)
            (dest / p.relative_to(path.parent)).write_bytes(p.read_bytes())
    return dest


def n8n_client() -> N8n | None:
    """An n8n client with the key from .env, or None (and a FAIL line) if n8n or the key does not work."""
    s = settings()
    n8n = N8n.from_settings()
    if not n8n.ready() or not n8n.key_works(s.n8n_api_key):
        print(f"FAIL  n8n at {s.n8n_url} does not answer, or N8N_API_KEY does not work. Run make up and make setup")
        return None
    return n8n


def find_workflow(flows: list[dict], uc: str, path: Path) -> tuple[str, dict | None]:
    """(name in the file, the n8n workflow with that name, else the active one on the file's webhook path)."""
    try:
        name = json.loads(path.read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError):
        name = ""
    hook = n8n_files.hook(uc, path)
    hit = next((w for w in flows if w["name"] == name), None) or next(
        (w for w in flows if w.get("active") and hook in webhook_paths(w)), None
    )
    return name, hit


def workflow_files(only_uc: str = "") -> list[tuple[str, Path]]:
    ucs = [only_uc] if only_uc else [uc for uc in usecases() if (USECASES_DIR / uc / "n8n").is_dir()]
    pairs = []
    for uc in ucs:
        folder = USECASES_DIR / uc / "n8n"
        pairs += [(uc, p) for p in sorted(folder.glob("*.json"))] or [(uc, folder / "workflow.json")]
    return pairs


def pull_one(uc: str, path: Path, full: dict, force: bool = False) -> tuple[bool, str]:
    """Save the published version of `full` to the files. Returns (done, text). Stops (done False) if the files
    changed since the last sync, unless `force` (then the files go to runs/n8n-backup/ first)."""
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    pub = published(full)
    exists = path.exists()
    state = sync_state(files_hash(path), workflow_hash(pub), imported_hashes().get(full["name"])) if exists else "n8n"
    if state in ("files", "both", "unknown") and not force:
        return False, sync_advice(state, uc, rel, full["name"])
    if state == "same" and not force:  # what the workflow does is the same: keep the files as they are
        remember_import(full["name"], pub)
        return True, f"{rel} already has it"
    note = ""
    if force and exists and state != "same":
        note = f" Your files before: {backup_files(uc, path).relative_to(ROOT)}"
    changed = save_workflow_files(path, pub)
    remember_import(full["name"], pub)  # n8n and the files are the same now
    names = ", ".join(str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p) for p in changed)
    return True, (f"saved to {names} (not committed)" if changed else f"{rel} already has it") + note


def n8n_pull(only_uc: str = "", force: bool = False) -> int:
    """`make n8n-pull`: n8n -> files, for the published version of each workflow. Never overwrites file
    edits made since the last sync, unless FORCE=1."""
    n8n = n8n_client()
    if not n8n:
        return 1
    flows, bad = n8n.workflows(), 0
    for uc, path in workflow_files(only_uc):
        name, hit = find_workflow(flows, uc, path)
        if hit is None:
            print(f"FAIL  {uc}: no workflow '{name}' (or webhook path '{n8n_files.hook(uc, path)}') in n8n")
            bad += 1
            continue
        full = n8n.api("GET", f"/workflows/{hit['id']}").json()
        done, text = pull_one(uc, path, full, force)
        print(f"{'OK  ' if done else 'STOP'}  {uc}: '{full['name']}': {text}")
        bad += not done
        if has_draft(full):
            print(f"WARN  {uc}: {draft_advice(uc, full['name'])}")
    for uc in {uc for uc, _ in workflow_files(only_uc)}:
        for p in unused_prompts(USECASES_DIR / uc / "n8n"):
            print(f"WARN  {uc}: no node uses {p.relative_to(ROOT)} any more (a node was renamed?): delete it")
    return 1 if bad else 0


def n8n_push(only_uc: str = "", force: bool = False) -> int:
    """`make n8n-push`: files -> n8n (fill the placeholders and prompts, update, publish)."""
    n8n = n8n_client()
    if not n8n:
        return 1
    cred = settings().n8n_openai_credential_id
    if not cred:
        print("FAIL  no N8N_OPENAI_CREDENTIAL_ID in .env. Run make setup first")
        return 1
    print(f"n8n ({settings().n8n_url})")
    KEPT.clear()
    return 0 if import_workflows(n8n, cred, force, only_uc) and not KEPT else 1


def n8n_status(uc: str, engine: str = "n8n") -> dict | None:
    """The sync state of the workflow of `uc` (n8n: workflow.json, n8n-agent: agent.json):
    {state, draft, name, path, full}. None if there is no key, n8n does not answer, or no such workflow."""
    path = n8n_files.workflow_file(uc, engine)
    if not settings().n8n_api_key or not path.exists():
        return None
    n8n = N8n.from_settings(timeout=10)
    try:
        _, hit = find_workflow(n8n.workflows(), uc, path)
        if hit is None:
            return None
        full = n8n.api("GET", f"/workflows/{hit['id']}").json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    state = sync_state(files_hash(path), workflow_hash(published(full)), imported_hashes().get(full["name"]))
    return {"state": state, "draft": has_draft(full), "name": full["name"], "path": path, "full": full}


def sync_before_eval(uc: str, engine: str = "n8n") -> str | None:
    """Before an n8n eval: if only n8n changed since the last sync, save it to the files and say so.
    If the files changed (alone or both), raise ValueError with what to run: the eval would not run them."""
    st = n8n_status(uc, engine)
    if not st or st["state"] == "same":
        return None
    if st["state"] != "n8n":
        rel = st["path"].relative_to(ROOT) if st["path"].is_relative_to(ROOT) else st["path"]
        raise ValueError(sync_advice(st["state"], uc, rel, st["name"]))
    done, text = pull_one(uc, st["path"], st["full"])
    return f"n8n changes {text}" if done else text


def main(cmd: str, force: bool = False, usecase: str = "") -> int:
    if cmd in ("n8n-pull", "n8n-export"):
        return n8n_pull(usecase, force)
    if cmd == "n8n-push":
        return n8n_push(usecase, force)
    return setup(force) if cmd == "setup" else check()


if __name__ == "__main__":
    # `python -m kit.setup profiles`: the --profile flags for docker compose (used by the Makefile).
    if sys.argv[1:] == ["profiles"]:
        print(" ".join(f"--profile {p}" for p in compose_profiles(read_env(ENV_FILE))))
    elif sys.argv[1:] == ["services"]:  # what `make up` does not start, and why
        for line in service_notes(read_env(ENV_FILE)):
            print(line)
    else:
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "setup"))
