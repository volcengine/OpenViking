"""Regression: OpenVikingService.reindex must construct ReindexExecutor with
account-scoped resolvers without TypeError (v0.5.2-hotfix backport of #6663)."""

from unittest.mock import patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.reindex_executor import ReindexExecutor
from openviking_cli.session.user_id import UserIdentifier


def test_executor_accepts_resolvers():
    vlm, vec = object(), object()
    ex = ReindexExecutor(vlm_resolver=vlm, vector_config_resolver=vec)
    assert ex.vlm_resolver is vlm and ex.vector_config_resolver is vec
    assert ReindexExecutor().vlm_resolver is None


@pytest.mark.asyncio
async def test_service_reindex_passes_account_resolvers():
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._vlm_resolver = object()
    service._vector_config_resolver = object()
    service._user = UserIdentifier("acct", "u")
    seen = {}

    async def fake_execute(self, **kwargs):
        seen["executor"] = self
        seen["kwargs"] = kwargs
        return {"status": "ok"}

    ctx = RequestContext(user=UserIdentifier("acct", "u"), role=Role.ROOT)
    with patch.object(ReindexExecutor, "execute", fake_execute):
        result = await service.reindex(uri="viking://resources/x", mode="vectors_only", ctx=ctx)
    assert result == {"status": "ok"}
    assert seen["executor"].vlm_resolver is service._vlm_resolver
    assert seen["executor"].vector_config_resolver is service._vector_config_resolver
