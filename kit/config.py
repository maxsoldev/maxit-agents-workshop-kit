"""Kit settings from `.env` at the repo root. The one place for ports, URLs and modes.

Every part of the kit (UI, runner, tools, setup) reads its settings from here, not from os.environ directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
USECASES_DIR = ROOT / "usecases"
RUNS_DIR = ROOT / "runs"
ENGINES = ("n8n", "claude", "python")

load_dotenv(ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    model: str            # default model for scaffolds and engines that read it
    judge_model: str      # model for `judge` checks
    ui_port: int
    tools_port: int
    n8n_mode: str         # local | hosted
    n8n_url: str          # base URL, e.g. http://localhost:5702
    n8n_api_key: str      # written by `kit setup` for local; given by the trainer for hosted
    langfuse_mode: str    # local | hosted | off
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    trains_url: str       # our MOTIS; empty = use the fallback only
    trains_fallback_url: str

    @property
    def tools_url(self) -> str:
        """MCP URL as the host sees it (Claude, Python, runner)."""
        return f"http://localhost:{self.tools_port}/mcp"

    @property
    def tools_url_docker(self) -> str:
        """MCP URL as n8n in Docker sees it."""
        return f"http://host.docker.internal:{self.tools_port}/mcp"

    @property
    def langfuse_on(self) -> bool:
        return self.langfuse_mode != "off" and bool(self.langfuse_public_key and self.langfuse_secret_key)


def settings() -> Settings:
    lf_mode = _env("LANGFUSE", "local").lower()
    n8n_mode = _env("N8N", "local").lower()
    return Settings(
        openai_api_key=_env("OPENAI_API_KEY"),
        model=_env("KIT_MODEL", "gpt-5-mini"),
        judge_model=_env("KIT_JUDGE_MODEL", "gpt-5-mini"),
        ui_port=int(_env("KIT_UI_PORT", "5700")),
        tools_port=int(_env("KIT_TOOLS_PORT", "5701")),
        n8n_mode=n8n_mode,
        n8n_url=_env("N8N_URL", "http://localhost:5702").rstrip("/"),
        n8n_api_key=_env("N8N_API_KEY"),
        langfuse_mode=lf_mode,
        langfuse_host=_env("LANGFUSE_HOST", "http://localhost:5703").rstrip("/"),
        langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
        trains_url=_env("TRAINS_URL").rstrip("/"),
        trains_fallback_url=_env("TRAINS_FALLBACK_URL", "https://api.transitous.org").rstrip("/"),
    )


def usecases() -> list[str]:
    """Use case folders that have a cases.jsonl."""
    return sorted(p.name for p in USECASES_DIR.iterdir() if (p / "cases.jsonl").exists()) if USECASES_DIR.exists() else []
