"""Fake Python agent for the runner tests: stdin JSON -> stdout JSON."""
import json
import sys

case = json.load(sys.stdin)
inp = case["input"]
print("fake agent log line", file=sys.stderr)
print('tool rules_get {"name": "routing"} -> "# Routing\\nVendor declined"', file=sys.stderr)
if inp["mode"] == "crash":
    sys.exit("fake agent crashed on purpose")
if inp["mode"] == "bad":
    print(json.dumps({"category": 5}))
else:
    print(json.dumps({"category": inp["want"], "reply": inp.get("note", "no note"), "tags": ["x"]}))
