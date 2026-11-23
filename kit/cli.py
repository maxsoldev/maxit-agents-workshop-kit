"""`kit` command: one entry point for every part. Each part owns its subcommand module.

kit setup [--force]  first-time setup: n8n account + key + credential + workflows, Langfuse check (kit/setup.py)
kit check            one case per use case and engine; prints OK or what to fix (kit/setup.py)
kit n8n-push [UC]    files -> n8n: usecases/<uc>/n8n/*.json and n8n/prompts/*.md, then publish (kit/setup.py)
kit n8n-pull [UC]    n8n -> files: the published workflows to usecases/<uc>/n8n/ (n8n-export = the same)
kit serve            UI on :5700 and tools MCP on :5701 in one process (kit/ui, kit/tools.py)
kit tools            only the tools MCP server (kit/tools.py)
kit tool NAME k=v    try one tool by hand and print its JSON (no name: list the tools)
kit eval UC ENGINE   run the cases of a use case on one engine (kit/runner)
kit run-case UC ENGINE CASE   run one case; print the engine input, output, log (stderr) and checks
kit compare RUN_A RUN_B       how run B changed against run A, per case
"""

from __future__ import annotations

import argparse
import sys

from kit.config import ALL_ENGINES


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kit", description="MaxIT agent kit")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("setup", help="first-time setup of n8n and Langfuse")
    st.add_argument("--force", action="store_true", help="load workflow.json into n8n even if n8n has edits")
    sub.add_parser("check", help="smoke check: one case per use case and engine")
    for cmd, text in [
        ("n8n-push", "files -> n8n: load usecases/<uc>/n8n/ (workflows and prompts) into n8n and publish"),
        ("n8n-pull", "n8n -> files: save the published n8n workflows to usecases/<uc>/n8n/"),
        ("n8n-export", "the same as n8n-pull"),
    ]:
        ex = sub.add_parser(cmd, help=text)
        ex.add_argument("usecase", nargs="?", default="", help="one use case (default: all)")
        ex.add_argument("--force", action="store_true", help="overwrite the other side even if it changed too")
    sub.add_parser("serve", help="UI and tools server")
    sub.add_parser("tools", help="only the tools MCP server")
    ev = sub.add_parser("eval", help="run an eval")
    ev.add_argument("usecase")
    ev.add_argument("engine", choices=ALL_ENGINES)
    ev.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ev.add_argument("--name", default="", help="run name (default: timestamp)")
    ev.add_argument("--holdout", action="store_true", help='also run the cases with "holdout": true')
    tl = sub.add_parser("tool", help="try a tool by hand: kit tool trains_search from_place=... date=...")
    tl.add_argument("name", nargs="?", default="", help="tool name (empty: list the tools)")
    tl.add_argument("args", nargs="*", help="key=value")
    cp = sub.add_parser("compare", help="compare two runs per case: kit compare <run_id before> <run_id after>")
    cp.add_argument("run_a")
    cp.add_argument("run_b")
    rc = sub.add_parser("run-case", help="run one case; print the engine input, output, log and checks")
    rc.add_argument("usecase")
    rc.add_argument("engine", choices=ALL_ENGINES)
    rc.add_argument("case_id")
    a = p.parse_args(argv)

    if a.cmd in ("setup", "check", "n8n-push", "n8n-pull", "n8n-export"):
        from kit import setup

        return setup.main(a.cmd, force=getattr(a, "force", False), usecase=getattr(a, "usecase", ""))
    if a.cmd == "serve":
        from kit.ui import app

        return app.serve()
    if a.cmd == "tools":
        from kit import tools

        return tools.serve()
    if a.cmd == "eval":
        from kit.runner import cli as runner_cli

        return runner_cli.main(a.usecase, a.engine, [c for c in a.cases.split(",") if c], a.name, a.holdout)
    if a.cmd == "tool":
        from kit import tools

        return tools.try_tool(a.name, a.args)
    if a.cmd == "run-case":
        from kit.runner import cli as runner_cli

        return runner_cli.run_one(a.usecase, a.engine, a.case_id)
    if a.cmd == "compare":
        from kit.runner import cli as runner_cli

        return runner_cli.compare_cli(a.run_a, a.run_b)
    return 2


if __name__ == "__main__":
    sys.exit(main())
