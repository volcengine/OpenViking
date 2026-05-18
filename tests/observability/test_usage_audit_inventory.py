# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.observability.usage_audit.inventory import ContextInventoryProvider
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct-1", user_id="user-1", agent_id="agent-1"),
        role=Role.USER,
    )


class FakeFSService:
    def __init__(self) -> None:
        self.calls = []

    async def stat(self, uri, *, ctx):
        self.calls.append((uri, ctx))
        return {
            "viking://resources": {"count": 2},
            "viking://agent/agent-1/skills": {"count": 3},
            "viking://user/user-1/memories": {"count": 5},
            "viking://agent/agent-1/memories": {"count": 7},
        }[uri]


class FailingFSService:
    async def stat(self, uri, *, ctx):
        raise RuntimeError(f"stat unavailable for {uri}")


class MissingFSService:
    async def stat(self, uri, *, ctx):
        raise AGFSNotFoundError(uri)


@pytest.mark.asyncio
async def test_context_inventory_counts_from_stat():
    fs = FakeFSService()
    provider = ContextInventoryProvider(
        SimpleNamespace(fs=fs),
        ttl_seconds=0,
    )
    ctx = _ctx()

    counts = await provider.get_counts(ctx)

    assert counts == {"files": 2, "skills": 3, "memories": 12, "total": 17}
    assert len(fs.calls) == 4
    assert {uri for uri, call_ctx in fs.calls if call_ctx is ctx} == {
        "viking://resources",
        "viking://agent/agent-1/skills",
        "viking://user/user-1/memories",
        "viking://agent/agent-1/memories",
    }


@pytest.mark.asyncio
async def test_context_inventory_treats_missing_roots_as_empty():
    provider = ContextInventoryProvider(
        SimpleNamespace(fs=MissingFSService()),
        ttl_seconds=0,
    )

    with patch("openviking.observability.usage_audit.inventory.logger.warning") as warning:
        counts = await provider.get_counts(_ctx())

    assert counts == {"files": 0, "skills": 0, "memories": 0, "total": 0}
    warning.assert_not_called()


@pytest.mark.asyncio
async def test_context_inventory_warns_on_unexpected_stat_failures():
    provider = ContextInventoryProvider(
        SimpleNamespace(fs=FailingFSService()),
        ttl_seconds=0,
    )

    with patch("openviking.observability.usage_audit.inventory.logger.warning") as warning:
        counts = await provider.get_counts(_ctx())

    assert counts == {"files": 0, "skills": 0, "memories": 0, "total": 0}
    warning.assert_called()
