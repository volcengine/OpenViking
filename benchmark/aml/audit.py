"""Bounded metadata-only request audit logs for the public AML adapter."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("benchmark.aml.audit")
logger.setLevel(logging.INFO)
_context: ContextVar[tuple[dict[str, Any], tuple[str, ...]] | None] = ContextVar(
    "aml_audit_context", default=None
)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        return open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            opener=lambda path, flags: os.open(path, flags, 0o600),
        )


def configure_audit_logging() -> None:
    """Persist up to 50 files of 20 MiB each when a log path is configured."""
    path = os.getenv("AML_AUDIT_LOG_FILE")
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = _PrivateRotatingFileHandler(
            target,
            maxBytes=20 * 1024 * 1024,
            backupCount=49,
            encoding="utf-8",
            errors="backslashreplace",
        )
        os.chmod(target, 0o600)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False


@contextmanager
def request_audit(*, secrets: tuple[str | None, ...], **fields: Any) -> Iterator[None]:
    token = _context.set((dict(fields), tuple(value for value in secrets if value)))
    try:
        yield
    finally:
        _context.reset(token)


def audit_fields(**fields: Any) -> None:
    context = _context.get()
    if context is not None:
        context[0].update(fields)


def audit_event(event: str, **fields: Any) -> None:
    context = _context.get()
    if context is None:
        return
    metadata, secrets = context

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
            # IDs are untrusted input; prevent multiline records and oversized log lines.
            return "".join(c if c.isprintable() else " " for c in value)[:512]
        return value

    record = {key: clean(value) for key, value in {**metadata, **fields}.items()}
    record.update(event=event, timestamp=datetime.now(timezone.utc).isoformat())
    logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
