"""Kit tools: one MCP server (name `kit`) on http://localhost:5701/mcp for all engines.

Each tool is a plain function with a docstring; the docstring is the tool description the model sees.
To add a tool, add a function with `@mcp.tool()`. Tools only read `usecases/*/data/` and send nothing.
On bad input a tool returns {"error": ..., "hint": ...}; it does not raise.
"""
from __future__ import annotations

import csv
import socket
import threading
import time
from datetime import date, datetime

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from kit import config
from kit.tools_lib import emails, events, trains

HOST = "127.0.0.1"  # Docker Desktop (macOS) reaches this as host.docker.internal; see docs/contracts.md §1

mcp = MCPServer("kit", instructions="Tools for the MaxIT workshop use cases. They read data; they send nothing.",
                log_level="WARNING")


def _data(usecase: str):
    return config.USECASES_DIR / usecase / "data"


def _error(error: str, hint: str) -> dict:
    return {"error": error, "hint": hint}


def _iso(value: str | None, name: str) -> date | None | dict:
    """Parse an optional ISO date; return an error dict if it is not one."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return _error(f"{name} is not a date: {value!r}", "Use YYYY-MM-DD, e.g. 2027-03-01.")


# ---------- marketing ----------

@mcp.tool()
def contacts_lookup(query: str) -> dict:
    """Find contacts in the bloxberg contact sheet.

    Searches name, organisation and email (not case-sensitive, part of a word is enough).
    Example: "Wollner", "MPI", "uni-example-milano". Returns all columns of each match.
    """
    q = (query or "").strip().lower()
    if not q:
        return _error("query is empty", "Give a name, organisation or email, e.g. 'Wollner'.")
    with open(_data("marketing") / "contacts.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    hits = [r for r in rows if any(q in (r.get(k) or "").lower() for k in ("Name", "Organisation", "Email"))]
    return {"count": len(hits), "contacts": hits}


@mcp.tool()
def inbox_history(sender: str, before: str | None = None) -> dict:
    """Earlier emails from one sender in the bloxberg info inbox, newest first.

    `sender`: email address or name (part is enough, not case-sensitive), e.g. "k.wollner@..." or "Wollner".
    `before`: optional date YYYY-MM-DD; only emails sent before that day.
    Each email: file, from, date, subject and the first 500 characters of the text.
    """
    s = (sender or "").strip().lower()
    if not s:
        return _error("sender is empty", "Give the sender's email address or name.")
    limit = _iso(before, "before")
    if isinstance(limit, dict):
        return limit
    found = []
    for path in sorted((_data("marketing") / "emails").glob("*.eml")):
        mail = emails.read_eml(path)
        if s not in mail["from"].lower():
            continue
        if limit and mail["date"] and date.fromisoformat(mail["date"][:10]) >= limit:
            continue
        mail["file"] = f"data/emails/{path.name}"
        mail["text"] = mail["text"][:500]
        found.append(mail)
    found.sort(key=lambda m: m["date"], reverse=True)
    return {"count": len(found), "emails": found}


# ---------- travel ----------

@mcp.tool()
def events_list(date_from: str | None = None, date_to: str | None = None) -> dict:
    """Events of the Digital Labs annual overview (2027), from the event sheet.

    Optional filter: `date_from` / `date_to` as YYYY-MM-DD; an event is kept when it overlaps that span.
    Each event has the raw sheet columns (German: Monat, Veranstaltung, Ort, Datum, Art, Fristen)
    plus `start` and `end` as ISO dates. Events without a date yet ("noch offen") are in `undated`.
    """
    lo, hi = _iso(date_from, "date_from"), _iso(date_to, "date_to")
    for v in (lo, hi):
        if isinstance(v, dict):
            return v
    with open(_data("travel") / "events.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    dated, undated = [], []
    for row in rows:
        start, end = events.parse_range(row.get("Datum", ""))
        if start is None:
            undated.append(row)
            continue
        if (lo and end < lo) or (hi and start > hi):
            continue
        dated.append({**row, "start": start.isoformat(), "end": end.isoformat()})
    return {"count": len(dated), "events": dated, "undated": undated}


@mcp.tool()
def trains_search(from_place: str, to_place: str, date: str, arrive_by: str | None = None,
                  depart_after: str | None = None, modes: str = "rail") -> dict:
    """Train connections between two places on one day (Germany and Europe), up to 5. No prices.

    `from_place`, `to_place`: station or city, e.g. "München Hbf", "Heidelberg Hbf".
    `date`: the day, YYYY-MM-DD. Times are local (Europe/Berlin), "HH:MM".
    `arrive_by`: arrive by then; you get the connections that arrive last before it (the latest first).
    `depart_after`: leave at or after then (default 06:00); the earliest first. Give one of the two.
    `modes`: "rail" (default: trains and S-Bahn, no buses) or "all" (also buses and coaches).
    Only connections that leave and arrive on that day. Each one: departure, arrival, duration, changes, trains, legs.
    Examples: to a 10:00 meeting: trains_search("München Hbf", "Berlin Hbf", "2026-12-08", arrive_by="10:00").
    Travel the evening before: trains_search("München Hbf", "Heidelberg Hbf", "2027-03-02", depart_after="16:00").
    """
    if not (from_place or "").strip() or not (to_place or "").strip():
        return _error("from_place and to_place are needed", "Example: 'München Hbf', 'Heidelberg Hbf'.")
    if arrive_by and depart_after:
        return _error("give arrive_by or depart_after, not both", "Example: arrive_by='10:00'.")
    text = (date or "").strip().replace("T", " ")
    try:
        day = datetime.strptime(text[:10], "%Y-%m-%d")
        hhmm = arrive_by or depart_after or (text[11:16] if len(text) >= 16 else "06:00")  # "YYYY-MM-DD HH:MM" too
        when = datetime.combine(day.date(), datetime.strptime(hhmm.strip(), "%H:%M").time(), trains.TZ)
    except ValueError:
        return _error(f"bad date or time: {date!r}, arrive_by={arrive_by!r}, depart_after={depart_after!r}",
                      "Use date YYYY-MM-DD and times HH:MM, e.g. '2026-12-07', arrive_by='10:00'.")
    s = config.settings()
    servers = list(dict.fromkeys(u for u in (s.trains_url, s.trains_fallback_url) if u))
    return trains.search(servers, from_place.strip(), to_place.strip(), when, bool(arrive_by), modes or "rail")


# ---------- both ----------

@mcp.tool()
def rules_get(usecase: str, name: str = "") -> dict:
    """Read a rules or process document of a use case (Markdown).

    `usecase`: "marketing" or "travel". `name`: document name without ".md", e.g. "routing", "travel_rules".
    With an empty or unknown name you get the list of names.
    """
    uc, nm = (usecase or "").strip(), (name or "").strip().removesuffix(".md")
    if any(bad in v for v in (uc, nm) for bad in ("/", "\\", "..")):
        return _error("paths are not allowed", "Give only a use case and a document name, e.g. 'travel', 'team'.")
    known = sorted(p.parent.name for p in config.USECASES_DIR.glob("*/data"))
    if uc not in known:
        return _error(f"unknown use case: {uc!r}", f"Use one of: {', '.join(known)}.")
    names = sorted(p.stem for p in _data(uc).glob("*.md"))
    if nm not in names:
        return _error(f"unknown document: {nm!r}" if nm else "no document name given", f"Use one of: {', '.join(names)}.")
    return {"usecase": uc, "name": nm, "text": (_data(uc) / f"{nm}.md").read_text(encoding="utf-8")}


# ---------- try a tool by hand: kit tool <name> key=value ... ----------

def try_tool(name: str, args: list[str]) -> int:
    """`kit tool trains_search from_place="München Hbf" to_place="Berlin Hbf" date=2026-12-08 arrive_by=10:00`:
    call the tool function and print its JSON. Without a name: list the tools and their parameters."""
    import asyncio
    import inspect
    import json

    names = [t.name for t in asyncio.run(mcp.list_tools())]
    if name not in names:
        print(f"Unknown tool {name!r}. Tools:" if name else "Tools:")
        for n in names:
            print(f"  {n}{inspect.signature(globals()[n])}")
        return 0 if not name else 1
    kwargs = {}
    for a in args:
        if "=" not in a:
            print(f"Give each argument as key=value, got {a!r}")
            return 1
        key, value = a.split("=", 1)
        kwargs[key] = value
    try:
        out = globals()[name](**kwargs)
    except TypeError as e:
        print(f"{e}. Parameters: {name}{inspect.signature(globals()[name])}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------- server ----------

def _server(log_level: str) -> uvicorn.Server:
    # The MCP SDK accepts only known Host headers; n8n in Docker sends host.docker.internal.
    security = TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", "http://host.docker.internal:*"],
    )
    app = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, host=HOST)
    port = config.settings().tools_port
    return uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level=log_level))


def _answers(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def serve() -> int:
    """Run the tools server in the foreground (`kit tools`). Stop with Ctrl+C."""
    s = config.settings()
    print(f"kit tools: {s.tools_url} (n8n in Docker: {s.tools_url_docker})", flush=True)
    _server("info").run()
    return 0


def start_background(timeout: float = 10.0) -> threading.Thread | None:
    """Start the server in a daemon thread (`kit serve`); return when the port answers.
    If the port already answers (e.g. `kit tools` runs), start nothing and return None."""
    port = config.settings().tools_port
    if _answers(port):
        return None
    server = _server("warning")
    t = threading.Thread(target=server.run, name="kit-tools", daemon=True)
    t.start()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if server.started:  # our server listens (not another process on the same port)
            return t
        if not t.is_alive():  # bind failed: someone else took the port in the meantime
            raise RuntimeError(f"port {port} is taken by another process")
        time.sleep(0.1)
    raise RuntimeError(f"tools server did not start on port {port}")
