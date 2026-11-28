# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression test for the dashboard summary skills counter.

Skills live under ``viking://resources/skills/`` (not under the per-user
namespace), so the inventory must stat that URI to surface a non-zero
``context_counts.skills`` on ``GET /api/v1/console/dashboard/summary``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.observability.usage_audit.inventory import ContextInventoryProvider
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct-1", user_id="user-1"),
        role=Role.ADMIN,
    )


class _SkillsFSService:
    """fs_service stub: skills exist at viking://resources/skills, user_root is empty."""

    def __init__(self, skill_count: int) -> None:
        self.skill_count = skill_count
        self.calls: list[str] = []

    async def stat(self, uri, *, ctx):
        self.calls.append(uri)
        if uri == "viking://resources":
            return {"count": 7}
        if uri == "viking://resources/skills":
            return {"count": self.skill_count}
        if uri == "viking://user/user-1/memories":
            return {"count": 4}
        if uri == "viking://user/user-1/skills":
            # Old (pre-fix) location — empty in this deployment.
            raise AGFSNotFoundError(uri)
        raise AssertionError(f"unexpected stat uri: {uri}")


@pytest.mark.asyncio
async def test_skills_counter_reads_resources_skills_root():
    fs = _SkillsFSService(skill_count=112)
    provider = ContextInventoryProvider(SimpleNamespace(fs=fs), ttl_seconds=0)

    counts = await provider.get_counts(_ctx())

    assert counts["skills"] == 112
    assert "viking://resources/skills" in fs.calls
    # Sanity: no regression in other counters or in the total.
    assert counts["files"] == 7
    assert counts["memories"] == 4
    assert counts["total"] == 7 + 112 + 4


@pytest.mark.asyncio
async def test_skills_counter_zero_when_no_skills_present():
    fs = _SkillsFSService(skill_count=0)
    provider = ContextInventoryProvider(SimpleNamespace(fs=fs), ttl_seconds=0)

    counts = await provider.get_counts(_ctx())

    assert counts["skills"] == 0
    assert counts["total"] == 7 + 0 + 4
