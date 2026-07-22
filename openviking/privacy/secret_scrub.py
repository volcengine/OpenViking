# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Secret-scrub gate for the memory extraction layer.

Raw session capture (messages.jsonl) intentionally keeps secrets verbatim —
that is the raw-capture contract. But once the extraction pipeline curates a
durable, vector-indexed memory, a surviving secret becomes retrievable across
future sessions via find/search (issue #2899). This module scrubs common
high-entropy secret shapes from curated memory content before it is persisted.

Opt-in by default (OPENVIKING_SECRET_SCRUB=1): secret detection has
false-positive risk against commit SHAs, content hashes, and long UUIDs, so
the gate ships disabled and is enabled where the operator accepts that
trade-off. The pattern list is configurable via env so internal key shapes
can be added without code changes.
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

# Each pattern targets a concrete, recognizable secret shape rather than
# generic "high-entropy blob" — the latter catches too many legitimate
# identifiers (UUIDs, SHAs, base64 payloads). Named shapes keep the
# false-positive rate low while still catching the secrets that actually
# leak in practice.
_DEFAULT_PATTERNS: Tuple[str, ...] = (
    r"sk-[A-Za-z0-9]{16,}",          # OpenAI-style API keys
    r"AQ[A-Za-z0-9_-]{20,}",         # Gemini-style keys
    r"xox[baprs]-[A-Za-z0-9-]+",     # Slack tokens
    r"Bearer\s+[A-Za-z0-9._-]{16,}", # Bearer headers (incl. the literal)
    r"gh[pousr]_[A-Za-z0-9]{36,}",   # GitHub fine-grained / PAT tokens
)

_REDACTED = "REDACTED_SECRET"

# Compile once at import; the pattern set is fixed unless overridden via env.
def _compile_patterns() -> List[re.Pattern]:
    raw = os.environ.get("OPENVIKING_SECRET_SCRUB_PATTERNS")
    if raw:
        # env overrides: newline-separated regexes
        items = [p.strip() for p in raw.splitlines() if p.strip()]
    else:
        items = list(_DEFAULT_PATTERNS)
    return [re.compile(p) for p in items]


def is_secret_scrub_enabled() -> bool:
    """Gate check. Defaults to OFF — see module docstring for the rationale."""
    return os.environ.get("OPENVIKING_SECRET_SCRUB", "").lower() in ("1", "true", "yes")


def scrub_secrets(text: str) -> Tuple[str, int]:
    """Scrub recognizable secret shapes from ``text``.

    Returns ``(scrubbed_text, redaction_count)``. When the gate is disabled
    (the default), returns the text unchanged with count 0.

    Idempotent: the REDACTED marker contains no secret shape, so re-running
    scrub on already-scrubbed text is a no-op.
    """
    if not text or not is_secret_scrub_enabled():
        return text, 0
    count = 0
    out = text
    for pat in _compile_patterns():
        out, n = pat.subn(_REDACTED, out)
        count += n
    return out, count
