"""`kit` command: one entry point for every part. Each part owns its subcommand module.

  kit setup            first-time setup: n8n account + key + credential + workflows, Langfuse check (kit/setup.py)
  kit check            one case per use case and engine; prints OK or what to fix (kit/setup.py)
  kit serve            UI on :5700 and tools MCP on :5701 in one process (kit/ui, kit/tools.py)
  kit tools            only the tools MCP server (kit/tools.py)
  kit eval UC ENGINE   run the cases of a use case on one engine (kit/runner)
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kit", description="MaxIT agent kit")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="first-time setup of n8n and Langfuse")
    sub.add_parser("check", help="smoke check: one case per use case and engine")
    sub.add_parser("serve", help="UI and tools server")
    sub.add_parser("tools", help="only the tools MCP server")
    ev = sub.add_parser("eval", help="run an eval")
    ev.add_argument("usecase")
    ev.add_argument("engine", choices=["n8n", "claude", "python"])
    ev.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ev.add_argument("--name", default="", help="run name (default: timestamp)")
    a = p.parse_args(argv)

    if a.cmd in ("setup", "check"):
        from kit import setup
        return setup.main(a.cmd)
    if a.cmd == "serve":
        from kit.ui import app
        return app.serve()
    if a.cmd == "tools":
        from kit import tools
        return tools.serve()
    if a.cmd == "eval":
        from kit.runner import cli as runner_cli
        return runner_cli.main(a.usecase, a.engine, [c for c in a.cases.split(",") if c], a.name)
    return 2


if __name__ == "__main__":
    sys.exit(main())
