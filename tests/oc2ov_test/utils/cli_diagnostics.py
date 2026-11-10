"""Public-safe diagnostics for OpenClaw subprocess failures."""

from __future__ import annotations

import re

MAX_DIAGNOSTIC_CHARS = 800

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 <redacted>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|authorization|access[_-]?token|token|secret)"
            r"(\s*[:=]\s*)([^\s,;]+)"
        ),
        r"\1\2<redacted>",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "<redacted>"),
)


def compact_subprocess_diagnostic(value: str, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    """Redact common credential shapes and bound subprocess output for CI logs."""
    compact = " ".join((value or "").split())
    for pattern, replacement in _SECRET_PATTERNS:
        compact = pattern.sub(replacement, compact)
    if not compact:
        return "<empty>"
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}...<truncated>"


def format_openclaw_cli_failure(message: str, stderr: str, returncode: int) -> str:
    """Build a bounded error without copying raw OpenClaw stderr."""
    diagnostic = compact_subprocess_diagnostic(stderr)
    return f"{message} (exit={returncode}; stderr={diagnostic})"
