from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking_cli.session.user_id import UserIdentifier


def _ctx():
    return RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)


@pytest.mark.asyncio
async def test_keywords_capability_check_propagates_metadata_failures():
    error = RuntimeError("metadata unavailable")
    check = AsyncMock(side_effect=error)
    vector_store = object()
    fs = SimpleNamespace(
        _get_vector_store=lambda: vector_store,
        _collection_has_fulltext=check,
    )

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx())

    check.assert_awaited_once_with(
        vector_store,
        _ctx(),
        supported_modes=("volcengine", "vikingdb"),
        raise_on_error=True,
    )
