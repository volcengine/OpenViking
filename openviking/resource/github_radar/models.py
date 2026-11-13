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
_DEFAULT_MAX_PENDING_PRS = 200
_DEFAULT_SUMMARY_BATCH_BYTES = 60_000
# Upper bounds reject values that pass the positive-int check but overflow
# timedelta at runtime (e.g. backfill_days=10**9 raises OverflowError on every
# trigger and processor_kwargs cannot be patched after watch creation).
_MAX_BACKFILL_DAYS = 3650  # ten years
_MAX_PRS_PER_RUN = 1000  # the 10-page x 100-row page guard cannot yield more
_MAX_PENDING_PRS = 10_000
_MAX_SUMMARY_BATCH_BYTES = 10_000_000  # 10 MB per LLM batch
_MAX_DIGEST_INTERVAL_MINUTES = 525_600  # one year
# Collection runs on every watch trigger (typically hourly); the digest window
# is independent and defaults to daily, so a large backfill catches up across
# hourly collection passes instead of spanning multiple daily reports.
_DEFAULT_DIGEST_INTERVAL_MINUTES = 24 * 60
_BODY_MAX_CHARS = 8000
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
STATE_FILENAME = ".radar-state.json"
SIGNALS_FILENAME = ".radar-signals.jsonl"
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
    max_pending_prs: int = _DEFAULT_MAX_PENDING_PRS
    summary_enabled: bool = False
    summary_batch_bytes: int = _DEFAULT_SUMMARY_BATCH_BYTES
    digest_interval_minutes: int = _DEFAULT_DIGEST_INTERVAL_MINUTES

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
        if isinstance(target_uri, str):
            target_uri = target_uri.strip()
            # Fail fast at watch creation: an invalid scheme would otherwise
            # survive validation and make every radar trigger fail permanently.
            # All valid targets (explicit or derived from the watch URI) are
            # ``viking://`` resources. A blank value means "derive from watch".
            if target_uri and not target_uri.startswith("viking://"):
                raise InvalidArgumentError(
                    f"{PR_RADAR_CONFIG_KEY}.target_uri must start with 'viking://', "
                    f"got {target_uri!r}"
                )
            if not target_uri:
                target_uri = None

        def _positive_int(name: str, default: int, maximum: int) -> int:
            value = raw.get(name, default)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise InvalidArgumentError(
                    f"{PR_RADAR_CONFIG_KEY}.{name} must be a positive integer"
                )
            if value > maximum:
                raise InvalidArgumentError(
                    f"{PR_RADAR_CONFIG_KEY}.{name} must be <= {maximum}"
                )
            return value

        backfill_days = _positive_int(
            "backfill_days", _DEFAULT_BACKFILL_DAYS, _MAX_BACKFILL_DAYS
        )
        max_prs_per_run = _positive_int(
            "max_prs_per_run", _DEFAULT_MAX_PRS_PER_RUN, _MAX_PRS_PER_RUN
        )
        max_pending_prs = _positive_int(
            "max_pending_prs", _DEFAULT_MAX_PENDING_PRS, _MAX_PENDING_PRS
        )
        summary_batch_bytes = _positive_int(
            "summary_batch_bytes",
            _DEFAULT_SUMMARY_BATCH_BYTES,
            _MAX_SUMMARY_BATCH_BYTES,
        )
        digest_interval_minutes = _positive_int(
            "digest_interval_minutes",
            _DEFAULT_DIGEST_INTERVAL_MINUTES,
            _MAX_DIGEST_INTERVAL_MINUTES,
        )
        return cls(
            enabled=enabled,
            target_uri=target_uri.strip() if isinstance(target_uri, str) else None,
            backfill_days=backfill_days,
            max_prs_per_run=max_prs_per_run,
            max_pending_prs=max_pending_prs,
            summary_enabled=bool(raw.get("summary_enabled", False)),
            summary_batch_bytes=summary_batch_bytes,
            digest_interval_minutes=digest_interval_minutes,
        )

    def first_run_floor(self) -> str:
        return fmt_ts(utc_now() - timedelta(days=self.backfill_days))


@dataclass
class RadarState:
    """Persistent watermark stored next to the reports (``.radar-state.json``).

    Two independent watermarks:

      * :attr:`issues_since` / :attr:`issues_seen` -- collection watermark,
        how far the gh issues endpoint has been read. GitHub's ``since`` query
        is inclusive (``>=``), so PRs whose ``updated_at`` equals the
        watermark are returned again on the next poll; their numbers are
        remembered in ``issues_seen`` and filtered instead of blindly bumping
        the watermark by a second.
      * :attr:`last_digest_at` -- digest watermark, the right edge of the last
        published report window. Empty means the initial backfill window has
        not been digested yet. Subsequent windows are anchored on the watch's
        ``created_at`` and advance by ``watch_interval`` per digest.
      * :attr:`scan_offset` -- endpoint rows already consumed at the current
        ``issues_since`` while a same-timestamp prefix is still being read;
        lets the next trigger resume past the issue-heavy tie prefix instead
        of re-reading page 1 forever. Reset to 0 whenever the watermark moves.
      * :attr:`partial_fragments` -- safety-valve partial digests already
        published for the current boundary, so later full-window reports can
        label themselves as continuations of those fragments.
    """

    issues_since: str = ""
    issues_seen: List[str] = field(default_factory=list)
    last_digest_at: str = ""
    scan_offset: int = 0
    partial_fragments: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "RadarState":
        if not isinstance(data, dict):
            return cls()
        seen = data.get("issues_seen")
        fragments = data.get("partial_fragments")
        offset = data.get("scan_offset", 0)
        return cls(
            issues_since=str(data.get("issues_since") or ""),
            issues_seen=[str(item) for item in seen] if isinstance(seen, list) else [],
            last_digest_at=str(data.get("last_digest_at") or ""),
            scan_offset=offset if isinstance(offset, int) and offset >= 0 else 0,
            partial_fragments=[
                dict(item) for item in fragments if isinstance(item, dict)
            ]
            if isinstance(fragments, list)
            else [],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issues_since": self.issues_since,
            "issues_seen": list(self.issues_seen),
            "last_digest_at": self.last_digest_at,
            "scan_offset": self.scan_offset,
            "partial_fragments": [dict(item) for item in self.partial_fragments],
        }


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

    _ROW_FIELDS = (
        "number",
        "title",
        "url",
        "state",
        "draft",
        "author",
        "created_at",
        "updated_at",
        "merged_at",
        "closed_at",
        "labels",
        "body",
    )

    def to_row(self) -> Dict[str, Any]:
        """JSON-safe dict for the append-only signal store (one jsonl line)."""
        return {name: getattr(self, name) for name in self._ROW_FIELDS}

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "PrSignal":
        return cls(**{name: row[name] for name in cls._ROW_FIELDS if name in row})

    @staticmethod
    def clip_body(raw: Any) -> str:
        if not isinstance(raw, str):
            return ""
        if len(raw) <= _BODY_MAX_CHARS:
            return raw
        return raw[:_BODY_MAX_CHARS].rstrip() + "\n…[truncated]"
