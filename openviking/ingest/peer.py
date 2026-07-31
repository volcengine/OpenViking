# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""peer_id resolution for replayed turns.

- assistant turns -> ``{harness}__{model}`` (or ``{harness}__{provider}__{model}``)
- user turns:
    * single-user dev harnesses (claude_code/codex/opencode) -> git identity of the
      session cwd repo, falling back to the configured OV user;
    * group-chat harnesses (hermes/openclaw) -> the original username from the log.

peer_id must match OpenViking's identifier rules (``[a-zA-Z0-9_.@-]+``). ASCII values
stay human-readable; anything else (e.g. CJK usernames) is base64-encoded as ``ext-…``,
mirroring vikingbot's external-peer convention.
"""

from __future__ import annotations

import base64
import re
import subprocess
from typing import Dict, Optional

from openviking.core.peer_id import safe_peer_id

# Join harness/provider/model with the same separator as the OV session id scheme.
SEP = "__"

_ALLOWED = re.compile(r"[^a-zA-Z0-9_.@-]+")
_GIT_PEER_CACHE: Dict[str, Optional[str]] = {}


def _sanitize_component(value: str) -> str:
    """Make one path-free component safe & readable (lossy for non-ASCII)."""
    cleaned = _ALLOWED.sub("-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return cleaned


def safe_external_peer(raw: Optional[str]) -> Optional[str]:
    """Return a valid peer_id for an arbitrary external identifier (readable if ASCII)."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    sanitized = _sanitize_component(text)
    if sanitized:
        pid = safe_peer_id(sanitized)
        if pid:
            return pid
    # Non-ASCII / unsanitizable -> stable, valid, unique fallback.
    encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")
    return safe_peer_id(f"ext-{encoded}")


def assistant_peer_id(
    harness: str, model: Optional[str], provider: Optional[str] = None
) -> Optional[str]:
    """`{harness}__{model}` or `{harness}__{provider}__{model}`."""
    parts = [_sanitize_component(harness)]
    if provider:
        parts.append(_sanitize_component(provider))
    parts.append(_sanitize_component(model) if model else "unknown")
    return safe_peer_id(SEP.join(p for p in parts if p))


def resolve_git_human_peer(cwd: Optional[str], fallback: str) -> Optional[str]:
    """Human peer_id from the cwd repo's git identity; fall back to ``fallback``.

    Prefers ``user.email`` (more unique), then ``user.name``. Cached per cwd.
    """
    if not cwd:
        return safe_external_peer(fallback)
    if cwd in _GIT_PEER_CACHE:
        cached = _GIT_PEER_CACHE[cwd]
        return cached if cached else safe_external_peer(fallback)

    identity: Optional[str] = None
    for key in ("user.email", "user.name"):
        try:
            out = subprocess.run(
                ["git", "-C", cwd, "config", "--get", key],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            break
        if out.returncode == 0 and out.stdout.strip():
            identity = out.stdout.strip()
            break

    resolved = safe_external_peer(identity) if identity else None
    _GIT_PEER_CACHE[cwd] = resolved
    return resolved if resolved else safe_external_peer(fallback)
