"""Train connections from a MOTIS 2 server (our own one, or the public Transitous API).

Transitous is run by volunteers: send a clear User-Agent, keep calls few, no retry loops.
Rules: https://transitous.org/api/ (no commercial use, about 720 /plan calls a day is "high usage").
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

USER_AGENT = "maxit-agent-kit/0.1 (MaxIT agents workshop; +https://transitous.org/api/)"
TIMEOUT = 10.0
TZ = ZoneInfo("Europe/Berlin")
MAX_CONNECTIONS = 5

# Same question, same answer: evals ask the same trip many times. Kept for the life of the process.
_cache: dict[tuple, object] = {}


def _get(base: str, path: str, params: dict):
    key = (base, path, tuple(sorted(params.items())))
    if key not in _cache:
        r = httpx.get(base + path, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        _cache[key] = r.json()
    return _cache[key]


def _geocode(base: str, place: str) -> dict:
    """Best stop for a place name (first hit of the MOTIS geocoder, stops first)."""
    hits = _get(base, "/api/v1/geocode", {"text": place, "type": "STOP"}) or \
        _get(base, "/api/v1/geocode", {"text": place})
    if not hits:
        raise LookupError(f"place not found: {place}")
    return {"id": hits[0]["id"], "name": hits[0]["name"]}


def _local(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def _connection(it: dict) -> dict:
    legs = [leg for leg in it["legs"] if leg["mode"] != "WALK"]
    mins = it["duration"] // 60
    return {
        "departure": _local(it["startTime"]),
        "arrival": _local(it["endTime"]),
        "duration": f"{mins // 60}h{mins % 60:02d}",
        "changes": it.get("transfers", max(0, len(legs) - 1)),
        "trains": [leg.get("displayName") or leg.get("tripShortName") or leg.get("routeShortName") or leg["mode"]
                   for leg in legs],
        "legs": [{"train": leg.get("displayName") or leg.get("tripShortName") or leg["mode"],
                  "from": leg["from"]["name"], "departure": _local(leg["startTime"]),
                  "to": leg["to"]["name"], "arrival": _local(leg["endTime"])} for leg in legs],
    }


def search_on(base: str, from_place: str, to_place: str, when: datetime, arrive_by: bool) -> dict:
    """One server: geocode both places, then plan. Raises on any error."""
    src, dst = _geocode(base, from_place), _geocode(base, to_place)
    plan = _get(base, "/api/v5/plan", {"fromPlace": src["id"], "toPlace": dst["id"], "time": when.isoformat(),
                                       "arriveBy": str(arrive_by).lower(), "numItineraries": MAX_CONNECTIONS})
    its = sorted(plan.get("itineraries") or [], key=lambda it: it["startTime"])
    return {"from": src["name"], "to": dst["name"],
            "connections": [_connection(it) for it in its[:MAX_CONNECTIONS]]}


def search(servers: list[str], from_place: str, to_place: str, when: datetime, arrive_by: bool) -> dict:
    """Try each server once, in order. The first answer with connections wins; it gets `source` (the URL).
    An error or an empty answer moves on to the next server."""
    errors, empty = [], None
    for base in servers:
        try:
            result = search_on(base, from_place, to_place, when, arrive_by)
        except (httpx.HTTPError, LookupError, KeyError, TypeError, ValueError) as e:
            errors.append(f"{base}: {e}")
            continue
        result["source"] = base
        if "transitous.org" in base:
            result["attribution"] = "Transitous, https://transitous.org/sources/"
        if result["connections"]:
            return result
        empty = empty or result
    if empty:
        return empty
    return {"error": ("no train data: " + "; ".join(errors)) if errors else "no train server set",
            "hint": "Check the place names (e.g. 'München Hbf'), or set TRAINS_URL / TRAINS_FALLBACK_URL in .env."}
