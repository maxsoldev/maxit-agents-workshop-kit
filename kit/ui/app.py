"""Kit UI: cases, run, results, runs. FastAPI + Jinja2 templates + htmx, no build step.

Start it with `uv run kit serve` (port 5700). All case and run data goes through `kit.runner`.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import importlib
import json
import re
import threading
from urllib.parse import quote
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kit import config

HERE = Path(__file__).resolve().parent
CHECK_TYPES = ("equals", "in", "contains", "not_contains", "count", "any", "judge")

app = FastAPI(title="MaxIT agent kit", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["pretty"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False)
templates.env.filters["compact"] = lambda v: json.dumps(v, ensure_ascii=False)
templates.env.filters["when"] = lambda v: str(v or "").replace("T", " ")[:16]  # ISO time -> "2026-11-24 10:05"


def runner():
    """The runner module (part C). Imported late, so tests can put a fake in sys.modules."""
    return importlib.import_module("kit.runner")


# ---------- helpers ----------

def usecases() -> list[str]:
    """Use case folders: each one with a cases.jsonl or a data/ folder."""
    root = config.USECASES_DIR
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "cases.jsonl").exists() or (p / "data").is_dir())


def uc_dir(uc: str) -> Path:
    if uc not in usecases():
        raise HTTPException(404, f"No use case '{uc}'")
    return config.USECASES_DIR / uc


def data_files(uc: str) -> list[str]:
    """All files in usecases/<uc>/data, as paths relative to the use case folder."""
    base = uc_dir(uc)
    return sorted(str(p.relative_to(base)) for p in (base / "data").rglob("*") if p.is_file() and not p.name.startswith("."))


# A chat message starts with a head line like "Priya Nair  18.11.2026, 10:30" (name, 2+ spaces, date, time).
MESSAGE_HEAD = re.compile(r"^(?P<who>\S.*?)\s{2,}(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4}),? (?P<t>\d{1,2}:\d{2})\s*$")


def chat_messages(path: Path) -> list[dict]:
    """The single messages of a chat file (e.g. mattermost_messages.txt): {"who", "date", "text"}.
    `text` is the head line plus the message, as in the starter cases."""
    out, cur = [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = MESSAGE_HEAD.match(line)
        if m:
            cur = {"who": m["who"], "date": f"{m['y']}-{int(m['m']):02d}-{int(m['d']):02d}", "text": line}
            out.append(cur)
        elif line.startswith("---"):
            cur = None
        elif cur is not None:
            cur["text"] += "\n" + line
    for c in out:
        c["text"] = c["text"].strip()
    return out


def email_date(path: Path) -> str:
    """The Date header of an .eml file as YYYY-MM-DD, or ""."""
    try:
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        return email.utils.parsedate_to_datetime(str(msg["date"])).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def input_picks(uc: str) -> list[dict]:
    """What the input picker lists: data files (no rule .md files), and each single message of a chat file.
    Each item: {"label", "kind": "file"|"message", "path" or "channel"+"text", "date"}."""
    base, picks = uc_dir(uc), []
    for rel in data_files(uc):
        path = base / rel
        if path.suffix == ".md":
            continue  # the owner's rules: the agent reads them with rules_get, they are not an input
        messages = chat_messages(path) if path.suffix == ".txt" else []
        if messages:
            channel = path.stem.split("_")[0]  # mattermost_messages.txt -> mattermost
            for m in messages:
                first = " ".join(m["text"].splitlines()[1:])[:70]
                picks.append({"label": f"{channel}: {m['who']}, {m['date']}: {first}", "kind": "message",
                              "channel": channel, "text": m["text"], "date": m["date"]})
            continue
        picks.append({"label": rel, "kind": "file", "path": rel,
                      "date": email_date(path) if path.suffix == ".eml" else ""})
    return picks


def field_words(path: str) -> str:
    """`tasks.0.kind` -> `tasks #1 → kind`; `options.*.trains` -> `every item of options → trains`."""
    out: list[str] = []
    for part in path.split("."):
        if part.isdigit():
            out[-1:] = [f"{out[-1] if out else ''} #{int(part) + 1}"]
        elif part == "*":
            out[-1:] = [f"every item of {out[-1]}" if out else "every item"]
        else:
            out.append(part)
    return " → ".join(out)


def check_words(c: dict, expected: dict) -> str:
    """One check in words, e.g. `is "leave"`, `has 1 to 3 items`, `does not contain "@"`."""
    def v(x):
        return json.dumps(x, ensure_ascii=False)

    t = c.get("type")
    if t == "equals":
        if "value" in c:
            return f"is {v(c['value'])}"
        want = expected
        for part in (c.get("field") or "").split("."):
            want = want.get(part) if isinstance(want, dict) else (
                want[int(part)] if isinstance(want, list) and part.isdigit() and int(part) < len(want) else None)
        return f"is {v(want)} (from expected)" if want is not None else "is … (no expected value: add one)"
    if t == "in":
        return "is one of " + ", ".join(v(x) for x in c.get("values") or [])
    if t == "contains":
        return f"contains {v(c.get('value'))}"
    if t == "not_contains":
        return f"does not contain {v(c.get('value'))}"
    if t == "count":
        if "equals" in c:
            return f"has exactly {c['equals']} item(s)"
        if "min" in c and "max" in c:
            return f"has {c['min']} to {c['max']} items"
        return f"has at least {c['min']} item(s)" if "min" in c else f"has at most {c.get('max')} item(s)"
    if t == "any":
        parts = [f"{k} is {v(x)}" for k, x in (c.get("where") or {}).items()]
        parts += [f"{k} contains {v(x)}" for k, x in (c.get("contains") or {}).items()]
        return "has an item where " + " and ".join(parts)
    if t == "judge":
        return f"judge (score {c.get('min', 4)} or more): {c.get('rubric', '')}"
    return t or "?"


def case_hints(uc: str, cases: list[dict]) -> dict:
    """Placeholders for the form from this use case's cases: the next id (t06) and a typical field."""
    nums = [re.fullmatch(r"([a-zA-Z]+)(\d+)", c.get("id", "")) for c in cases]
    nums = [m for m in nums if m]
    if nums:
        prefix, width = nums[-1][1], len(nums[-1][2])
        top = max(int(m[2]) for m in nums if m[1] == prefix)
        next_id = f"{prefix}{top + 1:0{width}d}"
    else:
        next_id = f"{uc[:1]}01"
    fields = [ch.get("field") for c in cases for ch in c.get("checks", []) if ch.get("field")]
    return {"id": next_id, "field": fields[0] if fields else "category"}


def safe_file(uc: str, rel: str) -> Path | None:
    """The file `rel` in the use case folder, or None if it is outside data/ or missing."""
    base = uc_dir(uc)
    p = (base / rel).resolve()
    return p if p.is_file() and p.is_relative_to((base / "data").resolve()) else None


def read_email(path: Path) -> dict:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    return {"from": str(msg["from"] or ""), "to": str(msg["to"] or ""), "date": str(msg["date"] or ""),
            "subject": str(msg["subject"] or ""), "body": body.get_content() if body else ""}


def file_previews(uc: str, data: dict | None) -> list[dict]:
    """For each `*_file` key in a case input: the file, and the email fields if it is an .eml file."""
    out = []
    for key, rel in (data or {}).items():
        if not (key.endswith("_file") and isinstance(rel, str)):
            continue
        p = safe_file(uc, rel)
        item = {"key": key, "path": rel, "missing": p is None, "email": None, "text": ""}
        if p and p.suffix == ".eml":
            item["email"] = read_email(p)
        elif p:
            item["text"] = p.read_text(encoding="utf-8", errors="replace")[:3000]
        out.append(item)
    return out


def check_label(c: dict) -> str:
    """Short text for a check chip, e.g. `contains reply "@"`."""
    t, f = c.get("type", "?"), c.get("field", "")
    if t == "in":
        return f"{f} in {json.dumps(c.get('values'), ensure_ascii=False)}"
    if t == "judge":
        return f"judge {f} (≥{c.get('min', 4)})"
    if t == "count":
        return f"count {f} " + " ".join(f"{k} {c[k]}" for k in ("equals", "min", "max") if k in c)
    if t == "any":
        return f"any {f} {json.dumps({k: c[k] for k in ('where', 'contains') if k in c}, ensure_ascii=False)}"
    if "value" in c:
        return f"{t} {f} {json.dumps(c['value'], ensure_ascii=False)}"
    return f"{t} {f}"


templates.env.filters["check_label"] = check_label
templates.env.filters["field_words"] = field_words
templates.env.globals["check_words"] = check_words


def page(request: Request, name: str, uc: str | None = None, status: int = 200, **ctx) -> HTMLResponse:
    s = config.settings()
    ctx.update(request=request, uc=uc, usecases=usecases(), engines=config.ENGINES, settings=s,
               langfuse_link=s.langfuse_mode != "off")
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def load_cases_safe(uc: str) -> tuple[list[dict], str | None]:
    """Cases of a use case, or ([], error text) if cases.jsonl is broken."""
    if not (uc_dir(uc) / "cases.jsonl").exists():
        return [], None
    try:
        return runner().load_cases(uc), None
    except Exception as e:  # show the error (e.g. a bad line) instead of a crash page
        return [], str(e)


# ---------- pages ----------

@app.get("/")
def index():
    ucs = usecases()
    return RedirectResponse(f"/uc/{ucs[0]}/cases" if ucs else "/docs", status_code=302)


@app.get("/uc/{uc}/cases", response_class=HTMLResponse)
def cases_page(request: Request, uc: str):
    cases, error = load_cases_safe(uc)
    rows = [{"case": c, "files": file_previews(uc, c.get("input")),
             "rest": {k: v for k, v in (c.get("input") or {}).items() if not k.endswith("_file")}} for c in cases]
    return page(request, "cases.html", uc, rows=rows, error=error)


@app.get("/uc/{uc}/cases/new", response_class=HTMLResponse)
def case_new(request: Request, uc: str, copy: str = ""):
    case = {"id": "", "title": "", "input": {}, "expected": {}, "checks": []}
    if copy:
        src = next((c for c in load_cases_safe(uc)[0] if c.get("id") == copy), None)
        if src:
            case = json.loads(json.dumps(src))
            case["id"] = f"{src['id']}-copy"
    return case_form(request, uc, case, original_id="")


@app.get("/uc/{uc}/cases/{case_id}/edit", response_class=HTMLResponse)
def case_edit(request: Request, uc: str, case_id: str):
    case = next((c for c in load_cases_safe(uc)[0] if c.get("id") == case_id), None)
    if case is None:
        raise HTTPException(404, f"No case '{case_id}'")
    return case_form(request, uc, case, original_id=case_id)


def case_form(request, uc, case, original_id, errors=None, raw=None, status=200):
    """The add / edit form. `raw` keeps the text the user typed when it did not parse."""
    raw = raw or {"input": json.dumps(case.get("input", {}), indent=2, ensure_ascii=False),
                  "expected": json.dumps(case.get("expected", {}), indent=2, ensure_ascii=False)}
    checks = [{"type": c.get("type", "equals"), "field": c.get("field", ""), "arg": check_arg(c),
               "min": c.get("min", "")} for c in case.get("checks", [])] \
        or [{"type": "equals", "field": "", "arg": "", "min": ""}]  # one empty row to start with
    cases = load_cases_safe(uc)[0]
    picks = input_picks(uc)
    has_messages = any(p["kind"] == "message" for p in picks) or any("message" in (c.get("input") or {}) for c in cases)
    return page(request, "case_form.html", uc, status=status, case=case, original_id=original_id, raw=raw,
                checks=checks, errors=errors or [], picks=picks, has_messages=has_messages, check_types=CHECK_TYPES,
                hints=case_hints(uc, cases),
                previews=file_previews(uc, case.get("input") if isinstance(case.get("input"), dict) else {}))


def check_arg(c: dict) -> str:
    """The one text box of a check row: value, values (as JSON list) or rubric."""
    if c.get("type") == "judge":
        return c.get("rubric", "")
    if c.get("type") == "in":
        return json.dumps(c.get("values", []), ensure_ascii=False)
    if c.get("type") == "count":
        limits = {k: c[k] for k in ("equals", "min", "max") if k in c}
        return str(limits["equals"]) if list(limits) == ["equals"] else json.dumps(limits)
    if c.get("type") == "any":
        return json.dumps({k: c[k] for k in ("where", "contains") if k in c}, ensure_ascii=False)
    if "value" not in c:
        return ""
    return c["value"] if isinstance(c["value"], str) else json.dumps(c["value"], ensure_ascii=False)


def parse_check(t: str, field: str, arg: str, min_: str) -> dict:
    c: dict = {"type": t, "field": field.strip()}
    arg = arg.strip()
    if t == "judge":
        c["rubric"] = arg
        if min_.strip():
            c["min"] = int(min_) if min_.strip().isdigit() else min_.strip()
    elif t == "in":
        try:
            c["values"] = json.loads(arg) if arg.startswith("[") else [v.strip() for v in arg.split(",") if v.strip()]
        except json.JSONDecodeError:
            c["values"] = arg  # validate_case reports it
    elif t == "equals" and arg:
        try:
            c["value"] = json.loads(arg)
        except json.JSONDecodeError:
            c["value"] = arg
    elif t in ("contains", "not_contains"):
        c["value"] = arg
    elif t in ("count", "any"):
        # count: `2` (equals) or {"min": 1, "max": 3}; any: {"where": {...}, "contains": {...}} or just {key: value}
        try:
            data = json.loads(arg) if arg else {}
        except json.JSONDecodeError:
            data = arg  # validate_case reports it
        if t == "count":
            c.update({"equals": data} if isinstance(data, int) else data if isinstance(data, dict) else {"equals": data})
        elif isinstance(data, dict) and not ({"where", "contains"} & set(data)):
            c["where"] = data
        elif isinstance(data, dict):
            c.update({k: v for k, v in data.items() if k in ("where", "contains")})
        else:
            c["where"] = data
    return c


@app.post("/uc/{uc}/cases/save", response_class=HTMLResponse)
async def case_save(request: Request, uc: str):
    f = await request.form()
    errors: list[str] = []
    raw = {"input": str(f.get("input", "")), "expected": str(f.get("expected", ""))}
    case: dict = {"id": str(f.get("id", "")).strip(), "title": str(f.get("title", "")).strip()}
    if str(f.get("today", "")).strip():
        case["today"] = str(f.get("today")).strip()
    if f.get("holdout"):
        case["holdout"] = True
    for key in ("input", "expected"):
        try:
            case[key] = json.loads(raw[key] or "{}")
        except json.JSONDecodeError as e:
            errors.append(f"{key.capitalize()} is not valid JSON (line {e.lineno}, column {e.colno}). Put double "
                          f'quotes " around names and texts, e.g. {{"category": "spam"}}, and a comma between two '
                          f"items. (Detail: {e.msg})")
            case[key] = {}
    if not case["title"]:
        errors.append("Title is empty: give the case a short name, e.g. \"Unknown fund wants a call\"")
    if raw["input"].strip() in ("", "{}"):
        errors.append("Input is empty: pick an email or a message and click Add to input, or paste a message")
    case["checks"] = [parse_check(str(t), str(fl), str(a), str(m)) for t, fl, a, m in
                      zip(f.getlist("check_type"), f.getlist("check_field"), f.getlist("check_arg"), f.getlist("check_min"))
                      if str(t) and (str(fl).strip() or str(a).strip())]
    original_id = str(f.get("original_id", ""))

    cases, load_error = load_cases_safe(uc)
    if load_error:
        errors.append(f"cases.jsonl cannot be read: {load_error}")
    if not errors:
        errors = list(runner().validate_case(case))
    if case["id"] != original_id and any(c.get("id") == case["id"] for c in cases):
        errors.append(f"id: '{case['id']}' is used by another case")
    if errors:
        return case_form(request, uc, case, original_id, errors=errors, raw=raw, status=422)

    i = next((n for n, c in enumerate(cases) if c.get("id") == original_id), None) if original_id else None
    if i is None:
        cases.append(case)
    else:
        cases[i] = case
    runner().save_cases(uc, cases)
    return RedirectResponse(f"/uc/{uc}/cases#case-{case['id']}", status_code=303)


@app.post("/uc/{uc}/cases/{case_id}/delete")
def case_delete(uc: str, case_id: str):
    cases, error = load_cases_safe(uc)
    if error:
        raise HTTPException(400, f"cases.jsonl cannot be read: {error}")
    runner().save_cases(uc, [c for c in cases if c.get("id") != case_id])
    return RedirectResponse(f"/uc/{uc}/cases", status_code=303)


@app.get("/uc/{uc}/file", response_class=HTMLResponse)
def file_preview(request: Request, uc: str, path: str = ""):
    """htmx partial: the preview of one data file (the email text for .eml files)."""
    previews = file_previews(uc, {"x_file": path}) if path else []
    return templates.TemplateResponse(request, "_previews.html", {"previews": previews})


@app.post("/uc/{uc}/previews", response_class=HTMLResponse)
async def input_previews(request: Request, uc: str):
    """htmx partial: the previews of all `*_file` keys in the input JSON of the form."""
    f = await request.form()
    try:
        data = json.loads(str(f.get("input", "")) or "{}")
    except json.JSONDecodeError:
        data = {}
    previews = file_previews(uc, data if isinstance(data, dict) else {})
    return templates.TemplateResponse(request, "_previews.html", {"previews": previews})


# ---------- run ----------

JOBS: dict[tuple[str, str], dict] = {}   # (uc, engine) -> live state of the last run
JOBS_LOCK = threading.Lock()


def start_job(uc: str, engine: str, case_ids: list[str] | None, name: str | None, cases: list[dict],
              holdout: bool = False) -> str | None:
    """Start a run in a background thread. Returns an error text if one runs already."""
    with JOBS_LOCK:
        old = JOBS.get((uc, engine))
        if old and not old["done"]:
            return f"A {engine} run for {uc} is still running. Wait until it ends."
        if case_ids:
            picked = [c for c in cases if c.get("id") in case_ids]
        else:  # the runner skips holdout cases unless asked
            picked = [c for c in cases if holdout or not c.get("holdout")]
        job = {"engine": engine, "name": name or "", "done": False, "error": None, "run_id": None,
               "cases": [{"id": c.get("id"), "title": c.get("title", ""), "status": "waiting"} for c in picked]}
        JOBS[(uc, engine)] = job

    def on_progress(ev: dict):
        for c in job["cases"]:
            if c["id"] == ev.get("case_id"):
                c["status"] = "running" if ev.get("status") == "running" else ("pass" if ev.get("passed") else "fail")
                if ev.get("error"):  # the engine did not answer: say why right here, not only on the results page
                    c["status"], c["error"] = "error", ev["error"]

    def work():
        try:
            run = runner().run_eval(uc, engine, case_ids=case_ids, name=name, on_progress=on_progress,
                                    holdout=holdout)
            job["run_id"] = run.get("run_id")
        except Exception as e:
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["done"] = True

    threading.Thread(target=work, daemon=True, name=f"run-{uc}-{engine}").start()
    return None


def default_engine(uc: str) -> str:
    """The engine of the last run of this use case (not `make check`), else claude; only KIT_ENGINES."""
    used = config.settings().engines
    try:
        runs = runner().list_runs(uc)
    except Exception:
        runs = []
    last = next((r.get("engine") for r in runs if r.get("name") != CHECK_RUN and r.get("engine") in used), None)
    return last or ("claude" if "claude" in used else used[0])


@app.get("/uc/{uc}/run", response_class=HTMLResponse)
def run_page(request: Request, uc: str, engine: str = "", error: str = ""):
    cases, load_error = load_cases_safe(uc)
    jobs = {e: JOBS[(uc, e)] for e in config.ENGINES if (uc, e) in JOBS}
    return page(request, "run.html", uc, cases=cases, error=error or load_error, jobs=jobs,
                engine=engine or default_engine(uc), run_engines=config.settings().engines)


@app.post("/uc/{uc}/run")
async def run_start(request: Request, uc: str):
    f = await request.form()
    engine = str(f.get("engine", ""))
    if engine not in config.ENGINES:
        raise HTTPException(400, f"Unknown engine '{engine}'")
    cases, error = load_cases_safe(uc)
    ids = [str(i) for i in f.getlist("case")] or None
    if not error:
        error = start_job(uc, engine, ids, str(f.get("name", "")).strip() or None, cases,
                          holdout=bool(f.get("holdout")))
    url = f"/uc/{uc}/run?engine={engine}" + (f"&error={quote(error)}" if error else "")
    return RedirectResponse(url, status_code=303)


@app.get("/uc/{uc}/run/progress", response_class=HTMLResponse)
def run_progress(request: Request, uc: str):
    """htmx partial, polled every second while a run is going."""
    jobs = {e: JOBS[(uc, e)] for e in config.ENGINES if (uc, e) in JOBS}
    return templates.TemplateResponse(request, "_progress.html", {"uc": uc, "jobs": jobs})


# ---------- runs and results ----------

CHECK_RUN = "check"  # the name `make check` gives its runs


@app.get("/uc/{uc}/runs", response_class=HTMLResponse)
def runs_page(request: Request, uc: str, checks: int = 0):
    """All runs; the `make check` runs only with ?checks=1."""
    uc_dir(uc)
    runs = runner().list_runs(uc)
    hidden = sum(1 for r in runs if r.get("name") == CHECK_RUN)
    if not checks:
        runs = [r for r in runs if r.get("name") != CHECK_RUN]
    for r, prev in zip(runs, runs[1:]):  # against the run before it: "13 cases (2 new, 1 changed)"
        now, before = r.get("case_hashes") or {}, prev.get("case_hashes") or {}
        r["new_cases"] = sum(1 for i in now if i not in before)
        r["changed_cases"] = sum(1 for i, h in now.items() if h and before.get(i) and before[i] != h)
    return page(request, "runs.html", uc, runs=runs, hidden=hidden, show_checks=bool(checks))


def compare_cases(run: dict, other: dict | None) -> dict:
    """Per case id: better / worse / same / new, this run against `other`."""
    if not other:
        return {}
    before = {c.get("id"): c.get("passed") for c in other.get("cases", [])}
    out = {}
    for c in run.get("cases", []):
        if c.get("id") not in before:
            out[c.get("id")] = "new"
        elif bool(c.get("passed")) == bool(before[c.get("id")]):
            out[c.get("id")] = "same"
        else:
            out[c.get("id")] = "better" if c.get("passed") else "worse"
    return out


def changed_cases(run: dict, other: dict | None) -> set:
    """Ids of cases that are not the same case in both runs (input, expected or checks changed)."""
    before = {c.get("id"): c.get("case_hash") for c in (other or {}).get("cases", [])}
    return {c.get("id") for c in run.get("cases", [])
            if c.get("case_hash") and before.get(c.get("id")) and before[c.get("id")] != c["case_hash"]}


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def results_page(request: Request, run_id: str, vs: str = ""):
    try:
        run = runner().load_run(run_id)
    except (FileNotFoundError, KeyError):
        raise HTTPException(404, f"No run '{run_id}'")
    uc = run.get("usecase")
    # Compare list: no `make check` runs; the same engine first; mark runs with other cases.
    ids = sorted(c.get("id", "") for c in run.get("cases", []))
    others = [r for r in runner().list_runs(uc) if r.get("run_id") != run_id and r.get("name") != CHECK_RUN]
    others.sort(key=lambda r: r.get("engine") != run.get("engine"))  # stable: newest first in each group
    for r in others:
        r["other_cases"] = r.get("case_ids") is not None and r.get("case_ids") != ids
    if not vs:  # default: the previous run of the same engine
        older = [r for r in others if r.get("engine") == run.get("engine") and r.get("run_id", "") < run_id]
        vs = older[0]["run_id"] if older else ""
    other = runner().load_run(vs) if vs and vs != "none" else None
    changes = compare_cases(run, other)
    counts = {k: sum(1 for v in changes.values() if v == k) for k in ("better", "worse", "same", "new")}
    changed = changed_cases(run, other)
    counts["changed"] = len(changed)
    for c in run.get("cases", []):
        c["_first_fail"] = c.get("error") or next(
            (ch.get("reason") for ch in c.get("checks", []) if ch.get("passed") is False), None)
        c["_files"] = file_previews(uc, c.get("input")) if uc in usecases() else []
    return page(request, "results.html", uc if uc in usecases() else None, run=run, others=others, vs=vs,
                other=other, changes=changes, counts=counts, changed=changed)


# ---------- docs ----------

def doc_files() -> dict[str, Path]:
    """Readable docs: README, docs/*.md and each use case BRIEF.md."""
    root = config.ROOT
    found = {"README.md": root / "README.md", "SETUP.md": root / "SETUP.md"}
    found.update({f"docs/{p.name}": p for p in sorted((root / "docs").glob("*.md"))})
    found.update({f"{uc}/BRIEF.md": config.USECASES_DIR / uc / "BRIEF.md" for uc in usecases()})
    return {k: v for k, v in found.items() if v.is_file()}


@app.get("/docs", response_class=HTMLResponse)
def docs_page(request: Request, name: str = ""):
    files = doc_files()
    name = name if name in files else next(iter(files), "")
    text = files[name].read_text(encoding="utf-8") if name else "No docs found."
    return page(request, "docs.html", None, files=list(files), doc=name, text=text)


def serve() -> int:
    """`kit serve`: start the tools MCP server (if part B is there), then the UI."""
    import socket

    import uvicorn

    from kit import tools

    s = config.settings()
    for port, what in ((s.ui_port, "UI"), (s.tools_port, "tools server")):
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                print(f"ERROR: port {port} ({what}) is taken. Is another `make serve`, `make check` or `kit tools` "
                      f"running? Stop it, then run make serve again.")
                return 1
    try:
        tools.start_background()
    except Exception as e:
        print(f"ERROR: the tools MCP server did not start: {e}. Stop the other process, then run make serve again.")
        return 1
    port = s.ui_port
    print(f"Kit UI: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0
