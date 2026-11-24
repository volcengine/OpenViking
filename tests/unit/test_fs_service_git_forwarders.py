"""Snapshot methods require initialized filesystem storage."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_methods_raise_when_not_initialized():
    svc = FSService()
    ctx = RequestContext(user=UserIdentifier("acc", "user"), role=Role.ROOT)
    with pytest.raises(NotInitializedError):
        await svc.commit(message="m", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.restore(project_dir="viking://x", source_commit="a", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.show("main", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.diff(path="viking://resources/a.md", from_ref=None, to_ref="main", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.log(ctx=ctx)
