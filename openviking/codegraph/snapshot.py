# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Snapshot source boundary for CodeGraph builds."""

from __future__ import annotations

from typing import Protocol, Sequence

from openviking.codegraph.models import SourceFile


class SourceSnapshotReader(Protocol):
    """Read source files from one immutable snapshot.

    Implementations own the integrity boundary: the returned files and blob
    object IDs must belong to ``source_snapshot_oid``.
    """

    def read_source_files(self, source_snapshot_oid: str) -> Sequence[SourceFile]:
        """Return all source files stored in the requested snapshot."""
