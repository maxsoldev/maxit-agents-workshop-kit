"""Langfuse tracing: one trace per case (contracts §6). Does nothing when `settings().langfuse_on` is false."""
from __future__ import annotations

import base64
import threading
from contextlib import contextmanager

from kit.config import settings

_client = None
_lock = threading.Lock()


def client():
    """One Langfuse client for the process, made on first use."""
    global _client
    with _lock:
        if _client is None:
            from langfuse import Langfuse

            s = settings()
            _client = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key, host=s.langfuse_host)
        return _client


class NoTrace:
    url = None

    def env(self, engine: str) -> dict:
        return {}

    def finish(self, output, error, checks, passed) -> None:
        pass


class CaseTrace(NoTrace):
    def __init__(self, lf, span, uc: str):
        self.lf, self.span, self.uc = lf, span, uc
        try:
            self.url = lf.get_trace_url(trace_id=span.trace_id)
        except Exception:
            self.url = None

    def env(self, engine: str) -> dict:
        """OTel env for Claude Code, so its spans nest under this case trace."""
        if engine != "claude":
            return {}
        s = settings()
        auth = base64.b64encode(f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()).decode()
        return {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{s.langfuse_host}/api/public/otel/v1/traces",
            "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic {auth}",
            "OTEL_RESOURCE_ATTRIBUTES": f"service.name=claude-code-{self.uc}",
            "TRACEPARENT": f"00-{self.span.trace_id}-{self.span.id}-01",
        }

    def finish(self, output, error, checks, passed) -> None:
        """Write the output and one score per check (1/0) plus `passed`."""
        try:
            self.span.update(output=output if error is None else {"error": error, "output": output},
                             level="ERROR" if error else "DEFAULT")
            for c in checks:
                name = c["type"] + (f".{c['field']}" if c["field"] else "")
                self.span.score_trace(name=name, value=1 if c["passed"] else 0, comment=c["reason"])
            self.span.score_trace(name="passed", value=1 if passed else 0)
        except Exception:
            pass  # tracing must never break a run


@contextmanager
def case_trace(uc: str, engine: str, run_id: str, run_name: str, case_id: str, engine_input: dict):
    """Context for one case. Yields an object with `.url`, `.env(engine)` and `.finish(...)`."""
    if not settings().langfuse_on:
        yield NoTrace()
        return
    from langfuse import propagate_attributes

    lf = client()
    name = f"{uc}/{case_id}"
    with lf.start_as_current_observation(name=name, input=engine_input) as span:
        with propagate_attributes(trace_name=name, session_id=run_id, tags=[uc, engine, run_name]):
            yield CaseTrace(lf, span, uc)


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass
