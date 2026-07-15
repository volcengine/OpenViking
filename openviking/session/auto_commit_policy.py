# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Automatic session-commit policy.

The policy applies to every session as a baseline configuration. There is no
per-session on/off switch: sessions (including existing ones) fall back to the
recommended defaults below when a field is missing. Configuration is only set
via session create / PATCH / GET, never per message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking_cli.exceptions import InvalidArgumentError

# Recommended defaults, aligned with the memory-bank streaming_write behavior.
DEFAULT_PENDING_TOKEN_THRESHOLD = 1000
DEFAULT_MESSAGE_COUNT_THRESHOLD = 50
DEFAULT_IDLE_TIMEOUT_SECONDS = 86400  # 1 day
DEFAULT_KEEP_RECENT_COUNT = 2
DEFAULT_MIN_COMMIT_INTERVAL_SECONDS = 60

# PRD upper bounds.
MAX_PENDING_TOKEN_THRESHOLD = 50000
MAX_MESSAGE_COUNT_THRESHOLD = 500
MAX_IDLE_TIMEOUT_SECONDS = 604800  # 7 days

_POLICY_KEYS = {
    "pending_token_threshold",
    "message_count_threshold",
    "idle_timeout_seconds",
    "keep_recent_count",
    "min_commit_interval_seconds",
}


def _coerce_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise InvalidArgumentError(f"auto_commit_policy.{field} must be an integer")
    if parsed < minimum:
        parsed = minimum
    if parsed > maximum:
        parsed = maximum
    return parsed


@dataclass
class AutoCommitPolicy:
    """Effective automatic-commit policy for one session."""

    pending_token_threshold: int = DEFAULT_PENDING_TOKEN_THRESHOLD
    message_count_threshold: int = DEFAULT_MESSAGE_COUNT_THRESHOLD
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    keep_recent_count: int = DEFAULT_KEEP_RECENT_COUNT
    min_commit_interval_seconds: int = DEFAULT_MIN_COMMIT_INTERVAL_SECONDS

    @classmethod
    def default(cls) -> "AutoCommitPolicy":
        return cls()

    @classmethod
    def from_dict(cls, data: Any) -> "AutoCommitPolicy":
        """Build a policy from a dict, clamping to PRD bounds and filling defaults."""
        if data is None:
            return cls.default()
        if isinstance(data, AutoCommitPolicy):
            return data
        if not isinstance(data, dict):
            raise InvalidArgumentError("auto_commit_policy must be an object")
        extra_keys = set(data) - _POLICY_KEYS
        if extra_keys:
            raise InvalidArgumentError(
                "auto_commit_policy supports only: " + ", ".join(sorted(_POLICY_KEYS))
            )
        default = cls.default()
        return cls(
            pending_token_threshold=_coerce_int(
                data.get("pending_token_threshold", default.pending_token_threshold),
                field="pending_token_threshold",
                minimum=0,
                maximum=MAX_PENDING_TOKEN_THRESHOLD,
            ),
            message_count_threshold=_coerce_int(
                data.get("message_count_threshold", default.message_count_threshold),
                field="message_count_threshold",
                minimum=0,
                maximum=MAX_MESSAGE_COUNT_THRESHOLD,
            ),
            idle_timeout_seconds=_coerce_int(
                data.get("idle_timeout_seconds", default.idle_timeout_seconds),
                field="idle_timeout_seconds",
                minimum=0,
                maximum=MAX_IDLE_TIMEOUT_SECONDS,
            ),
            keep_recent_count=_coerce_int(
                data.get("keep_recent_count", default.keep_recent_count),
                field="keep_recent_count",
                minimum=0,
                maximum=MAX_MESSAGE_COUNT_THRESHOLD,
            ),
            min_commit_interval_seconds=_coerce_int(
                data.get(
                    "min_commit_interval_seconds", default.min_commit_interval_seconds
                ),
                field="min_commit_interval_seconds",
                minimum=0,
                maximum=MAX_IDLE_TIMEOUT_SECONDS,
            ),
        )

    def merge(self, patch: Any) -> "AutoCommitPolicy":
        """Return a new policy with only the keys present in ``patch`` overwritten."""
        if patch is None:
            return self
        if not isinstance(patch, dict):
            raise InvalidArgumentError("auto_commit_policy must be an object")
        extra_keys = set(patch) - _POLICY_KEYS
        if extra_keys:
            raise InvalidArgumentError(
                "auto_commit_policy supports only: " + ", ".join(sorted(_POLICY_KEYS))
            )
        merged = {**self.to_dict(), **{k: patch[k] for k in patch if k in _POLICY_KEYS}}
        return AutoCommitPolicy.from_dict(merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_token_threshold": self.pending_token_threshold,
            "message_count_threshold": self.message_count_threshold,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "keep_recent_count": self.keep_recent_count,
            "min_commit_interval_seconds": self.min_commit_interval_seconds,
        }
