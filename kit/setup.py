"""`kit setup` and `kit check`.

setup: checks the host tools, sets up n8n (owner, API key, OpenAI credential, workflows) and checks Langfuse.
check: runs the first case of each use case on each engine and prints OK or what to fix.
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
from datetime import datetime
from pathlib import Path

import httpx

from kit.config import ENGINES, ROOT, RUNS_DIR, USECASES_DIR, settings, usecases

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
    """Compose profiles to start: n8n if N8N=local, langfuse if LANGFUSE=local (both default to local)."""
    out = []
    if env.get("N8N", "local").strip().lower() == "local":
        out.append("n8n")
    if env.get("LANGFUSE", "local").strip().lower() == "local":
        out.append("langfuse")
    return out


def fill_placeholders(text: str, credential_id: str, tools_url: str) -> str:
    """Put the real values into a workflow.json text."""
    return text.replace("{{OPENAI_CREDENTIAL_ID}}", credential_id).replace("{{KIT_TOOLS_URL}}", tools_url)


def workflow_body(wf: dict) -> dict:
    """Only the fields the n8n public API accepts on create and update."""
    return {"name": wf["name"], "nodes": wf["nodes"], "connections": wf.get("connections", {}),
            "settings": wf.get("settings") or {}}


def same_workflow(a: dict, b: dict) -> bool:
    """True if two workflows have the same nodes (name, type, version, parameters, credentials) and links."""
    def nodes(wf: dict) -> list:
        keys = ("name", "type", "typeVersion", "parameters", "credentials")
        return sorted((json.dumps({k: n.get(k) for k in keys}, sort_keys=True) for n in wf.get("nodes", [])))
    return nodes(a) == nodes(b) and json.dumps(a.get("connections", {}), sort_keys=True) == json.dumps(
        b.get("connections", {}), sort_keys=True)


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

def check_host() -> bool:
    print("Host")
    good = True
    if ENV_FILE.exists():
        ok(".env exists")
    else:
        fail(".env is missing. Run: cp .env.example .env  (then set OPENAI_API_KEY)")
        return False
    if settings().openai_api_key:
        ok("OPENAI_API_KEY is set")
    else:
        fail("OPENAI_API_KEY is empty. Put your key in .env, then run make setup again")
        good = False
    if (settings().n8n_mode == "local" or settings().langfuse_mode == "local"):
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


def setup_n8n() -> bool:
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
        r = n8n.http.post("/rest/owner/setup", json={"email": s.n8n_owner_email, "firstName": "Kit",
                                                     "lastName": "Owner", "password": s.n8n_owner_password})
        ok("owner account created" if r.status_code == 200 else "owner account exists")
        r = n8n.http.post("/rest/login", json={"emailOrLdapLoginId": s.n8n_owner_email,
                                               "password": s.n8n_owner_password})
        if r.status_code == 429:
            fail("n8n blocks logins for a short time (too many tries). Wait a minute, then run make setup again")
            return False
        if r.status_code != 200:
            fail(f"login as {s.n8n_owner_email} failed ({r.status_code}). Is N8N_OWNER_PASSWORD the one you used "
                 "first? If you do not know it: make reset, then make up and make setup")
            return False
        r = n8n.http.get("/rest/api-keys/scopes")
        scopes = r.json().get("data", []) if r.status_code == 200 else []
        r = n8n.http.post("/rest/api-keys", json={"label": f"kit-{int(time.time())}", "expiresAt": None,
                                                  "scopes": scopes})
        data = (r.json().get("data") or {}) if r.status_code == 200 else {}
        new = data.get("rawApiKey") or data.get("apiKey") or ""
        if not n8n.key_works(new):
            fail(f"could not create an API key ({r.status_code}). Create one in n8n: Settings > n8n API, "
                 "and put it in .env as N8N_API_KEY")
            return False
        set_env_key(ENV_FILE, "N8N_API_KEY", new)
        n8n.key = new
        ok("API key created and written to .env (N8N_API_KEY)")

    cred_id = setup_credential(n8n)
    if not cred_id:
        return False
    return import_workflows(n8n, cred_id)


def key_fingerprint(key: str) -> str:
    """Short hash of a key. n8n does not show a stored key, so .env keeps this to see if the key changed."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def setup_credential(n8n: N8n) -> str:
    """The OpenAI credential, made from OPENAI_API_KEY. Kept if it exists; its key is updated if it changed."""
    s = settings()
    if not s.openai_api_key:
        fail("no OpenAI credential: OPENAI_API_KEY is empty")
        return ""
    data = {"apiKey": s.openai_api_key, "url": "https://api.openai.com/v1", "header": False}
    fingerprint = key_fingerprint(s.openai_api_key)
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


def import_workflows(n8n: N8n, cred_id: str) -> bool:
    """Create or update each usecases/*/n8n/workflow.json by name, then activate it.
    Before an update, the old n8n copy goes to runs/n8n-backup/ (you may have edited it in n8n)."""
    files = sorted(USECASES_DIR.glob("*/n8n/workflow.json"))
    if not files:
        warn("no usecases/*/n8n/workflow.json to import yet")
        return True
    good = True
    by_name = {w["name"]: w for w in n8n.workflows()}
    for f in files:
        rel = f.relative_to(ROOT)
        try:
            wf = json.loads(fill_placeholders(f.read_text(), cred_id, settings().tools_url_docker))
            body = workflow_body(wf)
        except (ValueError, KeyError) as e:
            fail(f"{rel}: not a valid workflow ({e})")
            good = False
            continue
        old = by_name.get(body["name"])
        if old is None:
            r = n8n.api("POST", "/workflows", body)
            if r.status_code >= 300:
                fail(f"{rel}: import failed ({r.status_code}): {r.text[:200]}")
                good = False
                continue
            wid, state = r.json()["id"], "imported"
        else:
            wid = old["id"]
            full = n8n.api("GET", f"/workflows/{wid}").json()
            if same_workflow(full, body):
                state = "unchanged"
            else:
                backup = RUNS_DIR / "n8n-backup" / f"{f.parent.parent.name}-{datetime.now():%Y%m%d-%H%M%S}.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(json.dumps(full, indent=2))
                if full.get("active"):
                    n8n.api("POST", f"/workflows/{wid}/deactivate")
                r = n8n.api("PUT", f"/workflows/{wid}", body)
                if r.status_code >= 300:
                    fail(f"{rel}: update failed ({r.status_code}): {r.text[:200]}")
                    good = False
                    continue
                state = f"updated from the file (old copy: {backup.relative_to(ROOT)})"
            if full.get("active") and state == "unchanged":
                ok(f"{body['name']}: {state}, active")
                continue
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
    switch = ("To go on without local Langfuse, set in .env: LANGFUSE=off (no traces), or LANGFUSE=hosted with "
              "LANGFUSE_HOST and the keys from the trainer. Then run make up and make setup again")

    def healthy() -> bool:
        try:
            return httpx.get(f"{s.langfuse_host}/api/public/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    if not wait_for(healthy, LANGFUSE_WAIT_S if s.langfuse_mode == "local" else 10):
        fail(f"Langfuse is not healthy at {s.langfuse_host}. See make logs. {switch}")
        return False
    try:
        r = httpx.get(f"{s.langfuse_host}/api/public/projects", timeout=10,
                      auth=(s.langfuse_public_key, s.langfuse_secret_key))
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
    if s.n8n_mode == "local":
        print(f"  n8n        {s.n8n_url}   login {s.n8n_owner_email}, password N8N_OWNER_PASSWORD in .env")
    else:
        print(f"  n8n        {s.n8n_url}   (hosted)")
    if s.langfuse_mode == "local":
        print(f"  Langfuse   {s.langfuse_host}   login {s.langfuse_user_email}, password LANGFUSE_INIT_USER_PASSWORD in .env")
    elif s.langfuse_mode == "hosted":
        print(f"  Langfuse   {s.langfuse_host}   (hosted)")
    else:
        print("  Langfuse   off")


def setup() -> int:
    host = check_host()
    if not ENV_FILE.exists():
        return 1
    n8n = setup_n8n()
    lf = setup_langfuse()
    summary()
    if host and n8n and lf:
        print("\nSetup done. Next: make serve (keep it running), then make check in a second terminal.")
        return 0
    print("\nSetup is not complete: fix the FAIL lines above, then run make setup again.")
    return 1


# ---------- kit check ----------

NEXT_STEP = {
    "n8n": "is n8n up (make up) and the workflow active (make setup)? Is the tools server on (make serve)?",
    "claude": "run `claude` once and log in; is the tools server on (make serve)?",
    "python": "is OPENAI_API_KEY set? Run the agent by hand to see its log on stderr",
}


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
    if not _port_open(s.tools_port):
        try:
            from kit import tools
            tools.start_background()
            wait_for(lambda: _port_open(s.tools_port), 15)
            print(f"Started the tools server on :{s.tools_port} for this check.")
        except ImportError:
            print(f"WARN  the tools server is not on :{s.tools_port}; tool calls will fail. Run make serve")
    bad = 0
    for uc in usecases():
        lines = (USECASES_DIR / uc / "cases.jsonl").read_text().splitlines()
        first = next((json.loads(x)["id"] for x in lines if x.strip()), None)
        if not first:
            print(f"SKIP  {uc}: cases.jsonl has no cases")
            continue
        for engine in ENGINES:
            label = f"{uc}/{engine} ({first})"
            if not (USECASES_DIR / uc / engine).exists():
                print(f"SKIP  {label}: no usecases/{uc}/{engine}/ folder")
                continue
            if engine == "n8n" and not s.n8n_api_key and s.n8n_mode == "local":
                print(f"FAIL  {label}: n8n is not set up. Next: make up, make setup")
                bad += 1
                continue
            try:
                run = run_eval(uc, engine, case_ids=[first], name="check")
                case = run["cases"][0]
            except Exception as e:  # the check must go on to the next engine
                print(f"FAIL  {label}: {type(e).__name__}: {e}. Next: {NEXT_STEP[engine]}")
                bad += 1
                continue
            if case.get("passed"):
                print(f"OK    {label}")
                continue
            reason = case.get("error") or "; ".join(
                f"{c['type']} {c.get('field', '')}: {c.get('reason', '')}" for c in case.get("checks", [])
                if not c.get("passed"))
            if case.get("error"):
                bad += 1
                print(f"FAIL  {label}: {reason}. Next: {NEXT_STEP[engine]}")
            else:  # the engine answered; only checks failed. That is normal for a scaffold.
                print(f"OK    {label}: the engine works, some checks fail ({reason[:200]})")
    print("\nAll engines answer." if not bad else f"\n{bad} engine(s) failed. Fix them, then run make check again.")
    return 1 if bad else 0


def main(cmd: str) -> int:
    return setup() if cmd == "setup" else check()


if __name__ == "__main__":
    # `python -m kit.setup profiles`: the --profile flags for docker compose (used by the Makefile).
    if sys.argv[1:] == ["profiles"]:
        print(" ".join(f"--profile {p}" for p in compose_profiles(read_env(ENV_FILE))))
    else:
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "setup"))
