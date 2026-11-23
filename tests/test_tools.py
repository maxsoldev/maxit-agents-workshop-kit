"""Tests for kit/tools.py. No network, except the test marked `live` (run: uv run pytest -q -m live)."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from kit import tools
from kit.tools_lib import events, trains


def test_contacts_lookup_by_name_org_email():
    assert tools.contacts_lookup("WOLLNER")["contacts"][0]["Email"] == "k.wollner@mpi-example-biology.example"
    assert tools.contacts_lookup("uni-example-milano")["count"] == 1
    assert tools.contacts_lookup("MPI")["count"] >= 2
    assert tools.contacts_lookup("nobody-here")["count"] == 0
    assert "error" in tools.contacts_lookup("  ")


def test_inbox_history_newest_first():
    r = tools.inbox_history("k.wollner@mpi-example-biology.example")
    assert r["count"] == 2
    assert r["emails"][0]["date"] > r["emails"][1]["date"]
    assert set(r["emails"][0]) == {"file", "from", "date", "subject", "text"}
    assert all(len(m["text"]) <= 500 for m in r["emails"])


def test_inbox_history_before_and_folded_header():
    assert tools.inbox_history("Wollner", before="2026-11-03")["count"] == 1
    assert tools.inbox_history("Krüger")["count"] == 1  # From header folded over two lines, RFC 2047
    assert "error" in tools.inbox_history("Wollner", before="next week")
    assert "error" in tools.inbox_history("")


def test_html_only_part_to_text():
    msg = (
        b"From: A <a@x.example>\nSubject: Hi\nDate: Mon, 02 Nov 2026 09:00:00 +0100\n"
        b"Content-Type: text/html; charset=utf-8\n\n<html><style>p{}</style><p>Hello <b>there</b></p></html>"
    )
    import email
    from email import policy

    assert tools.emails.body_text(email.message_from_bytes(msg, policy=policy.default)) == "Hello there"


@pytest.mark.parametrize(
    "text, start, end",
    [
        ("14.01.2027", date(2027, 1, 14), date(2027, 1, 14)),
        ("02.–03.02.2027", date(2027, 2, 2), date(2027, 2, 3)),
        ("30.06.–02.07.2027", date(2027, 6, 30), date(2027, 7, 2)),
        ("noch offen", None, None),
    ],
)
def test_parse_range(text, start, end):
    assert events.parse_range(text) == (start, end)


def test_events_list_filter():
    allr = tools.events_list()
    assert allr["count"] == 10 and len(allr["undated"]) == 2
    r = tools.events_list("2027-03-01", "2027-03-31")
    assert [e["Veranstaltung"] for e in r["events"]] == ["E-Science-Tage 2027"]
    assert r["events"][0]["start"] == "2027-03-03"
    # overlap: LIBER runs 30.06.–02.07.
    assert "LIBER Annual Conference" in [e["Veranstaltung"] for e in tools.events_list("2027-07-01")["events"]]
    assert "error" in tools.events_list("März")


def test_kit_tool_by_hand(capsys):
    assert tools.try_tool("rules_get", ["usecase=travel", "name=team"]) == 0
    assert '"name": "team"' in capsys.readouterr().out
    assert tools.try_tool("", []) == 0 and "trains_search(" in capsys.readouterr().out
    assert tools.try_tool("nope", []) == 1
    assert tools.try_tool("rules_get", ["travel"]) == 1  # not key=value


def test_rules_get():
    r = tools.rules_get("travel", "travel_rules")
    assert r["text"].strip()
    assert tools.rules_get("marketing", "routing.md")["name"] == "routing"
    assert "routing" in tools.rules_get("marketing", "nope")["hint"]
    assert "error" in tools.rules_get("sales", "x")
    for bad in [("travel", "../marketing/data/routing"), ("../travel", "team"), ("travel", "a/b")]:
        assert tools.rules_get(*bad)["error"] == "paths are not allowed"


# ---------- trains, with a fake MOTIS ----------

PLAN = {
    "itineraries": [
        {
            "duration": 10860,
            "startTime": "2026-12-07T05:20:00Z",
            "endTime": "2026-12-07T08:21:00Z",
            "transfers": 0,
            "legs": [
                {
                    "mode": "WALK",
                    "from": {"name": "a"},
                    "to": {"name": "b"},
                    "startTime": "2026-12-07T05:20:00Z",
                    "endTime": "2026-12-07T05:23:00Z",
                },
                {
                    "mode": "HIGHSPEED_RAIL",
                    "displayName": "ICE 1096",
                    "from": {"name": "München Hbf"},
                    "to": {"name": "Heidelberg Hbf"},
                    "startTime": "2026-12-07T05:23:00Z",
                    "endTime": "2026-12-07T08:21:00Z",
                },
            ],
        },
    ]
}


def fake_get(fail_hosts=()):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        assert headers["User-Agent"].startswith("maxit-agents-workshop-kit")
        if any(h in url for h in fail_hosts):
            raise httpx.ConnectTimeout("timeout")
        body = [{"id": "S1", "name": params["text"]}] if "geocode" in url else PLAN
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    return get, calls


@pytest.fixture(autouse=True)
def clear_cache():
    trains._cache.clear()


def test_trains_search_parses_plan(monkeypatch):
    get, calls = fake_get()
    monkeypatch.setattr(trains.httpx, "get", get)
    monkeypatch.setenv("TRAINS_URL", "")
    r = tools.trains_search("München Hbf", "Heidelberg Hbf", "2026-12-07", arrive_by="10:00")
    c = r["connections"][0]
    assert (c["departure"], c["arrival"], c["duration"], c["changes"]) == (
        "2026-12-07 06:20",
        "2026-12-07 09:21",
        "3h01",
        0,
    )
    assert c["trains"] == ["ICE 1096"]
    assert r["source"] == "https://api.transitous.org"
    assert len(calls) == 3  # geocode, geocode, plan
    tools.trains_search("München Hbf", "Heidelberg Hbf", "2026-12-07", arrive_by="10:00")
    assert len(calls) == 3  # second time from the cache


def test_trains_search_falls_back(monkeypatch):
    get, calls = fake_get(fail_hosts=("motis.local",))
    monkeypatch.setattr(trains.httpx, "get", get)
    monkeypatch.setenv("TRAINS_URL", "http://motis.local:8080")
    r = tools.trains_search("München Hbf", "Heidelberg Hbf", "2026-12-07 06:00")
    assert r["source"] == "https://api.transitous.org" and r["connections"]
    assert sum("motis.local" in u for u in calls) == 1  # one try, no retry loop


def _it(start: str, end: str, name: str) -> dict:
    return {
        "duration": 3600,
        "startTime": start,
        "endTime": end,
        "transfers": 0,
        "legs": [
            {
                "mode": "HIGHSPEED_RAIL",
                "displayName": name,
                "from": {"name": "A"},
                "to": {"name": "B"},
                "startTime": start,
                "endTime": end,
            }
        ],
    }


def test_trains_search_filters_to_the_day_and_time(monkeypatch):
    plan = {
        "itineraries": [
            _it("2026-12-07T21:00:00Z", "2026-12-07T23:00:00Z", "day before"),
            _it("2026-12-08T05:00:00Z", "2026-12-08T07:00:00Z", "early"),
            _it("2026-12-08T06:00:00Z", "2026-12-08T08:30:00Z", "late"),
            _it("2026-12-08T08:00:00Z", "2026-12-08T09:30:00Z", "too late"),
        ]
    }
    seen = []

    def get(url, params=None, headers=None, timeout=None):
        seen.append(params)
        body = [{"id": "S1", "name": params["text"]}] if "geocode" in url else plan
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(trains.httpx, "get", get)
    monkeypatch.setenv("TRAINS_URL", "")
    r = tools.trains_search("A", "B", "2026-12-08", arrive_by="10:00")
    assert [c["trains"][0] for c in r["connections"]] == ["late", "early"]  # before 10:00 local, latest first
    assert seen[-1]["arriveBy"] == "true" and seen[-1]["transitModes"] == "RAIL,SUBURBAN"
    r = tools.trains_search("A", "B", "2026-12-08", depart_after="07:00", modes="all")
    assert [c["trains"][0] for c in r["connections"]] == ["late", "too late"]
    assert seen[-1]["transitModes"] == "TRANSIT"
    assert "error" in tools.trains_search("A", "B", "2026-12-08", arrive_by="10:00", depart_after="07:00")


def test_trains_search_all_fail_and_bad_input(monkeypatch):
    get, _ = fake_get(fail_hosts=("motis.local", "transitous"))
    monkeypatch.setattr(trains.httpx, "get", get)
    monkeypatch.setenv("TRAINS_URL", "http://motis.local:8080")
    r = tools.trains_search("München Hbf", "Heidelberg Hbf", "2026-12-07")
    assert "error" in r and "hint" in r
    assert "error" in tools.trains_search("München Hbf", "Heidelberg Hbf", "7.12.2026")
    assert "error" in tools.trains_search("München Hbf", "Heidelberg Hbf", "2026-12-07", arrive_by="10 Uhr")
    assert "error" in tools.trains_search("", "Heidelberg Hbf", "2026-12-07")


@pytest.mark.live
def test_trains_search_live(monkeypatch):
    monkeypatch.setenv("TRAINS_URL", "")
    r = tools.trains_search("München Hbf", "Heidelberg Hbf", "2026-12-07", arrive_by="10:00")
    assert r["connections"], r
    assert all(c["arrival"] <= "2026-12-07 10:00" for c in r["connections"])


# ---------- through MCP ----------


def test_tools_listed_over_mcp():
    from mcp import Client

    async def names():
        async with Client(tools.mcp) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(names()) >= {"contacts_lookup", "inbox_history", "events_list", "trains_search", "rules_get"}
