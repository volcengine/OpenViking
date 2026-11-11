# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Data models and config parsing for the GitHub PR radar.

The radar rides the existing watch cadence, so its config has no period field:
``watch_interval`` is the single clock. Config travels inside the watch task's
``processor_kwargs`` under the reserved :data:`PR_RADAR_CONFIG_KEY`, which keeps
the watch store schema (file and MySQL backends) untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from openviking_cli.exceptions import InvalidArgumentError

PR_RADAR_CONFIG_KEY = "pr_radar"

_DEFAULT_BACKFILL_DAYS = 7
_DEFAULT_MAX_PRS_PER_RUN = 50
_BODY_MAX_CHARS = 8000
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
STATE_FILENAME = ".radar-state.json"
LATEST_REPORT_NAME = "latest.md"
REPORTS_DIRNAME = "reports"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(moment: datetime) -> str:
    """GitHub emits fixed-width UTC timestamps; keep the same shape on disk."""
    return moment.astimezone(timezone.utc).strftime(_TS_FMT)


def parse_ts(text: Any) -> Optional[datetime]:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def norm_ts(text: Any) -> str:
    parsed = parse_ts(text)
    return fmt_ts(parsed) if parsed else ""


def escape_repo_slug(repo_path: str) -> str:
    """Escape an ``org/repo`` identity into a single URI-safe directory segment.

    ``volcengine/OpenViking`` -> ``volcengine__OpenViking``. Dots and dashes are
    legal URI segments and are preserved.
    """
    return str(repo_path).strip().strip("/").replace("/", "__")


@dataclass(frozen=True)
class RadarConfig:
    """Validated radar config extracted from ``processor_kwargs.pr_radar``."""

    enabled: bool = False
    target_uri: Optional[str] = None
    backfill_days: int = _DEFAULT_BACKFILL_DAYS
    max_prs_per_run: int = _DEFAULT_MAX_PRS_PER_RUN

    @classmethod
    def from_kwargs(cls, processor_kwargs: Optional[Dict[str, Any]]) -> "RadarConfig":
        raw = (processor_kwargs or {}).get(PR_RADAR_CONFIG_KEY)
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise InvalidArgumentError(
                f"{PR_RADAR_CONFIG_KEY} must be an object, got {type(raw).__name__}"
            )
        enabled = bool(raw.get("enabled", False))
        target_uri = raw.get("target_uri")
        if target_uri is not None and not isinstance(target_uri, str):
            raise InvalidArgumentError(f"{PR_RADAR_CONFIG_KEY}.target_uri must be a string")

        def _positive_int(name: str, default: int) -> int:
            value = raw.get(name, default)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise InvalidArgumentError(
                    f"{PR_RADAR_CONFIG_KEY}.{name} must be a positive integer"
                )
            return value

        backfill_days = _positive_int("backfill_days", _DEFAULT_BACKFILL_DAYS)
        max_prs_per_run = _positive_int("max_prs_per_run", _DEFAULT_MAX_PRS_PER_RUN)
        return cls(
            enabled=enabled,
            target_uri=target_uri.strip() if isinstance(target_uri, str) else None,
            backfill_days=backfill_days,
            max_prs_per_run=max_prs_per_run,
        )

    def first_run_floor(self) -> str:
        return fmt_ts(utc_now() - timedelta(days=self.backfill_days))


@dataclass
class RadarState:
    """Persistent watermark stored next to the reports (``.radar-state.json``).

    GitHub's ``since`` query is inclusive (``>=``), so PRs whose ``updated_at``
    equals the watermark are returned again on the next poll. Their numbers are
    remembered in :attr:`issues_seen` and filtered instead of blindly bumping the
    watermark by a second, which would silently skip same-second updates.
    """

    issues_since: str = ""
    issues_seen: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "RadarState":
        if not isinstance(data, dict):
            return cls()
        seen = data.get("issues_seen")
        return cls(
            issues_since=str(data.get("issues_since") or ""),
            issues_seen=[str(item) for item in seen] if isinstance(seen, list) else [],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"issues_since": self.issues_since, "issues_seen": list(self.issues_seen)}


@dataclass(frozen=True)
class PrSignal:
    """One pull request observed through the issues endpoint."""

    number: int
    title: str
    url: str
    state: str
    draft: bool
    author: str
    created_at: str
    updated_at: str
    merged_at: Optional[str]
    closed_at: Optional[str]
    labels: List[str]
    body: str

    @property
    def source_ref(self) -> str:
        return f"#{self.number}"

    @staticmethod
    def clip_body(raw: Any) -> str:
        if not isinstance(raw, str):
            return ""
        if len(raw) <= _BODY_MAX_CHARS:
            return raw
        return raw[:_BODY_MAX_CHARS].rstrip() + "\n…[truncated]"
