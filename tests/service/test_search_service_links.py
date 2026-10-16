# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{}, {"include_links": True}])
async def test_search_service_preserves_link_options(flags):
    fs = SimpleNamespace(search=AsyncMock(return_value="result"))
    service = SearchService(fs)
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role.USER)

    assert await service.search("race", ctx=ctx, **flags) == "result"
    kwargs = fs.search.await_args.kwargs
    for key in ("include_links",):
        assert kwargs.get(key) == flags.get(key)
