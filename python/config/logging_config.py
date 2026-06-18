"""
config/logging_config.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Configures structured logging for the entire application.

- Development: human-readable coloured output
- Production (LOG_JSON=true): JSON lines for log aggregators (Datadog, CloudWatch, etc.)

Call setup_logging() once at application startup (in api/main.py lifespan).
Every other module just does:  logger = logging.getLogger("vision_i.module_name")
"""

import logging
import sys
from typing import Any, Dict

from config.settings import settings


class _JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter â€” no external deps."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from core.utils import utcnow_iso
        
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            span_context = span.get_span_context()
            trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else None
            span_id = format(span_context.span_id, "016x") if span_context.is_valid else None
        except ImportError:
            trace_id, span_id = None, None

        payload: Dict[str, Any] = {
            "ts":      utcnow_iso(),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class _DevFormatter(logging.Formatter):
    """Coloured human-readable formatter for development."""

    _COLOURS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self._COLOURS.get(record.levelname, "")
        reset  = self._RESET
        ts     = self.formatTime(record, "%H:%M:%S")
        name   = record.name.replace("vision_i.", "")
        msg    = record.getMessage()
        line   = f"{ts}  {colour}{record.levelname:<8}{reset}  {name:<30}  {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging() -> None:
    """
    Call once at startup. Configures the root 'vision_i' logger and
    suppresses noisy third-party loggers.
    """
    level     = getattr(logging, settings.log_level, logging.INFO)
    formatter = _JsonFormatter() if settings.log_json else _DevFormatter()

    handler = logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        if sys.platform == "win32" else sys.stdout
    )
    handler.setFormatter(formatter)

    root = logging.getLogger("vision_i")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False
    for noisy in ("urllib3", "requests", "httpx", "uvicorn.access",
                  "apscheduler", "neo4j", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

