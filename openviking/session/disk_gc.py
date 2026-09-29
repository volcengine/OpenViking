# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure planning logic for opt-in session disk retention (GC).

This module owns only the decision layer: given a disk-side snapshot of
sessions (URIs, last-activity timestamps, archive mtimes) it produces a
conservative list of deletion candidates. Execution happens elsewhere and
must go through ``VikingFS.rm(recursive=True)`` so vector index entries
are cleaned up in lockstep with the filesystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

# Commit archives live under ``<session>/history/archive_NNN``.
ARCHIVE_DIR_RE = re.compile(r"archive_(\d+)")

REASON_ARCHIVE_OVER_AGE = "archive_over_age"
REASON_SESSION_IDLE_EXPIRED = "session_idle_expired"


@dataclass(frozen=True)
class ArchiveInfo:
    """One ``history/archive_NNN`` directory inside a session."""

    name: str
    mtime: Optional[datetime] = None

    @property
    def index(self) -> Optional[int]:
        match = ARCHIVE_DIR_RE.fullmatch(self.name)
        return int(match.group(1)) if match else None


@dataclass(frozen=True)
class SessionDiskState:
    """Disk-side snapshot of one session, gathered by the GC executor."""

    uri: str
    session_id: str
    last_activity: Optional[datetime] = None
    is_active: bool = False
    archives: tuple = ()


@dataclass(frozen=True)
class GcAction:
    """One deletion candidate produced by :func:`plan_disk_gc`."""

    uri: str
    session_id: str
    reason: str


def plan_disk_gc(
    sessions: Iterable[SessionDiskState],
    *,
    now: datetime,
    archive_max_age_days: float = 0.0,
    max_idle_days: float = 0.0,
) -> List[GcAction]:
    """Plan conservative disk-retention deletions for the given sessions.

    Rules (all opt-in; a threshold of ``0`` disables that rule):

    - Sessions flagged ``is_active`` are skipped entirely.
    - When ``max_idle_days`` is positive and ``last_activity`` is known, a
      session idle for longer than the threshold is planned for whole
      deletion. Its archives are then not listed separately.
    - When ``archive_max_age_days`` is positive, archives with a known mtime
      older than the threshold are planned for deletion, except the
      highest-numbered archive which is always kept so a session never loses
      its most recent commit context.
    - Anything not explicitly covered above (unknown mtime, active session,
      fresh data) is left untouched.
    """
    actions: List[GcAction] = []
    archive_cutoff = timedelta(days=archive_max_age_days) if archive_max_age_days > 0 else None
    idle_cutoff = timedelta(days=max_idle_days) if max_idle_days > 0 else None

    for session in sessions:
        if session.is_active:
            continue

        if (
            idle_cutoff is not None
            and session.last_activity is not None
            and (now - session.last_activity) > idle_cutoff
        ):
            actions.append(
                GcAction(
                    uri=session.uri,
                    session_id=session.session_id,
                    reason=REASON_SESSION_IDLE_EXPIRED,
                )
            )
            continue

        if archive_cutoff is None:
            continue

        indexes = [a.index for a in session.archives if a.index is not None]
        newest_index = max(indexes) if indexes else None
        for archive in session.archives:
            if archive.index is None or archive.mtime is None:
                # Unclassifiable -> keep, never guess from missing metadata.
                continue
            if archive.index == newest_index:
                continue
            if (now - archive.mtime) > archive_cutoff:
                actions.append(
                    GcAction(
                        uri=f"{session.uri.rstrip('/')}/history/{archive.name}",
                        session_id=session.session_id,
                        reason=REASON_ARCHIVE_OVER_AGE,
                    )
                )
    return actions
