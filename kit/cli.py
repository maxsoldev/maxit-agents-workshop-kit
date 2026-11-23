"""`kit` command: one entry point for every part. Each part owns its subcommand module.

  kit setup [--force]  first-time setup: n8n account + key + credential + workflows, Langfuse check (kit/setup.py)
  kit check            one case per use case and engine; prints OK or what to fix (kit/setup.py)
  kit n8n-export [UC]  save the n8n workflows (your editor changes) to usecases/<uc>/n8n/workflow.json
  kit serve            UI on :5700 and tools MCP on :5701 in one process (kit/ui, kit/tools.py)
  kit tools            only the tools MCP server (kit/tools.py)
  kit eval UC ENGINE   run the cases of a use case on one engine (kit/runner)
  kit run-case UC ENGINE CASE   run one case; print the engine input, output, log (stderr) and checks
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kit", description="MaxIT agent kit")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("setup", help="first-time setup of n8n and Langfuse")
    st.add_argument("--force", action="store_true", help="load workflow.json into n8n even if n8n has edits")
    sub.add_parser("check", help="smoke check: one case per use case and engine")
    ex = sub.add_parser("n8n-export", help="save the n8n workflows to usecases/<uc>/n8n/workflow.json")
    ex.add_argument("usecase", nargs="?", default="", help="one use case (default: all)")
    sub.add_parser("serve", help="UI and tools server")
    sub.add_parser("tools", help="only the tools MCP server")
    ev = sub.add_parser("eval", help="run an eval")
    ev.add_argument("usecase")
    ev.add_argument("engine", choices=["n8n", "claude", "python"])
    ev.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ev.add_argument("--name", default="", help="run name (default: timestamp)")
    rc = sub.add_parser("run-case", help="run one case; print the engine input, output, log and checks")
    rc.add_argument("usecase")
    rc.add_argument("engine", choices=["n8n", "claude", "python"])
    rc.add_argument("case_id")
    a = p.parse_args(argv)

    if a.cmd in ("setup", "check", "n8n-export"):
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
        return runner_cli.main(a.usecase, a.engine, [c for c in a.cases.split(",") if c], a.name)
    if a.cmd == "run-case":
        from kit.runner import cli as runner_cli
        return runner_cli.run_one(a.usecase, a.engine, a.case_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
