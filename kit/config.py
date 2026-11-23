"""Kit settings from `.env` at the repo root. The one place for ports, URLs and modes.

Every part of the kit (UI, runner, tools, setup) reads its settings from here, not from os.environ directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
USECASES_DIR = ROOT / "usecases"
RUNS_DIR = ROOT / "runs"
ENGINES = ("n8n", "claude", "python")


def _read_env_file() -> dict[str, str]:
    path = ROOT / ".env"
    return {k: v or "" for k, v in dotenv_values(path).items()} if path.exists() else {}


# At start, copy .env into os.environ (for tools that read the env). Remember what came from the file:
# settings() reads .env again each time, so a new key works without a restart.
# Skip empty values: `OPENAI_BASE_URL=` must mean "not set", but the OpenAI SDK takes "" as the base URL.
_FROM_FILE = {k: v for k, v in _read_env_file().items() if k not in os.environ and v}
os.environ.update(_FROM_FILE)
_file_now: dict[str, str] = {}


def _env(name: str, default: str = "") -> str:
    """The shell env (or a value set in this process) wins; else the .env file as it is now."""
    if name in os.environ and os.environ[name] != _FROM_FILE.get(name):
        return os.environ[name].strip()
    return _file_now.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_base_url: str  # empty = OpenAI; else any OpenAI-compatible gateway (LiteLLM, Azure, company proxy)
    model: str           # default model for scaffolds and engines that read it
    judge_model: str      # model for `judge` checks
    ui_port: int
    tools_port: int
    n8n_mode: str         # local | hosted
    n8n_url: str          # base URL, e.g. http://localhost:5702
    n8n_api_key: str      # written by `kit setup` for local; given by the trainer for hosted
    n8n_owner_email: str  # local: owner login that `kit setup` creates
    n8n_owner_password: str
    n8n_openai_credential_id: str  # written by `kit setup`: the n8n credential made from OPENAI_API_KEY
    langfuse_mode: str    # local | hosted | off
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_user_email: str  # local: the login Langfuse creates at first start
    trains_url: str       # our MOTIS; empty = use the fallback only
    trains_fallback_url: str
    engines: tuple[str, ...]  # KIT_ENGINES: the engines this team uses (default all); setup and check skip the rest

    @property
    def tools_url(self) -> str:
        """MCP URL as the host sees it (Claude, Python, runner)."""
        return f"http://localhost:{self.tools_port}/mcp"

    @property
    def tools_url_docker(self) -> str:
        """MCP URL as n8n in Docker sees it."""
        return f"http://host.docker.internal:{self.tools_port}/mcp"

    @property
    def openai_base_url_docker(self) -> str:
        """Model API URL as n8n in Docker sees it (a gateway on this computer is host.docker.internal there)."""
        url = self.openai_base_url or "https://api.openai.com/v1"
        return url.replace("//localhost", "//host.docker.internal").replace("//127.0.0.1", "//host.docker.internal")

    @property
    def langfuse_on(self) -> bool:
        return self.langfuse_mode != "off" and bool(self.langfuse_public_key and self.langfuse_secret_key)


def settings() -> Settings:
    """Read the settings now (the .env file again each call, so edits work without a restart)."""
    global _file_now
    _file_now = _read_env_file()
    lf_mode = _env("LANGFUSE", "local").lower()
    n8n_mode = _env("N8N", "local").lower()
    return Settings(
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL").rstrip("/"),
        model=_env("KIT_MODEL", "gpt-6-luna"),
        judge_model=_env("KIT_JUDGE_MODEL", "gpt-6.1-terra"),
        ui_port=int(_env("KIT_UI_PORT", "5700")),
        tools_port=int(_env("KIT_TOOLS_PORT", "5701")),
        n8n_mode=n8n_mode,
        n8n_url=_env("N8N_URL", "http://localhost:5702").rstrip("/"),
        n8n_api_key=_env("N8N_API_KEY"),
        n8n_owner_email=_env("N8N_OWNER_EMAIL", "kit@example.com"),
        n8n_owner_password=_env("N8N_OWNER_PASSWORD"),
        n8n_openai_credential_id=_env("N8N_OPENAI_CREDENTIAL_ID"),
        langfuse_mode=lf_mode,
        langfuse_host=_env("LANGFUSE_HOST", "http://localhost:5703").rstrip("/"),
        langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
        langfuse_user_email=_env("LANGFUSE_INIT_USER_EMAIL", "kit@example.com"),
        trains_url=_env("TRAINS_URL").rstrip("/"),
        trains_fallback_url=_env("TRAINS_FALLBACK_URL", "https://api.transitous.org").rstrip("/"),
        engines=parse_engines(_env("KIT_ENGINES")),
    )


def parse_engines(text: str) -> tuple[str, ...]:
    """`n8n,claude` -> ("n8n", "claude"), in the order of ENGINES. Empty or no known name = all engines."""
    names = {p.strip().lower() for p in text.split(",")}
    return tuple(e for e in ENGINES if e in names) or ENGINES


def usecases() -> list[str]:
    """Use case folders that have a cases.jsonl."""
    return sorted(p.name for p in USECASES_DIR.iterdir() if (p / "cases.jsonl").exists()) if USECASES_DIR.exists() else []
