# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared fixtures for unit tests."""

from datetime import datetime, timezone

from openviking.core.context import Context


def make_test_context(
    uri: str,
    abstract: str = "abstract",
    active_count: int = 1,
    updated_at: datetime | None = None,
) -> Context:
    """Build a minimal Context for tests via Context.from_dict.

    Centralized so tests do not duplicate construction or rely on
    Context.__new__ tricks. Using from_dict means tests stay correct as
    Context fields evolve.
    """
    return Context.from_dict(
        {
            "uri": uri,
            "abstract": abstract,
            "active_count": active_count,
            "updated_at": (
                updated_at.isoformat()
                if updated_at is not None
                else datetime(2026, 4, 1, tzinfo=timezone.utc).isoformat()
            ),
        }
    )
