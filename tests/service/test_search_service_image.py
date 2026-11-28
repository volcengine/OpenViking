import asyncio
import base64
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking.utils.model_retry import effective_max_retries
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self):
        self.image_url = None
        self.retry_values = {}
        self.find_started = asyncio.Event()
        self.release_find = asyncio.Event()

    async def read_file_bytes(self, uri, ctx=None):
        assert uri == "viking://resources/cat.png"
        return b"\x89PNG\r\n\x1a\n"

    async def find(self, **kwargs):
        self.image_url = kwargs["image_url"]
        self.find_started.set()
        await self.release_find.wait()
        self.retry_values["find"] = effective_max_retries(3)
        return {"ok": True}

    async def search(self, **kwargs):
        self.retry_values["search"] = effective_max_retries(3)
        return {"ok": True}


@pytest.mark.asyncio
async def test_image_search_resolves_uri_and_isolates_retry_scope():
    fs = FakeVikingFS()
    service = SearchService(fs)
    ctx = RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)

    search = asyncio.create_task(
        service.find(query="", image_url="viking://resources/cat.png", ctx=ctx),
    )
    try:
        await asyncio.wait_for(fs.find_started.wait(), 1)
        await fs.search(query="concurrent background work")
        fs.release_find.set()
        result = await asyncio.wait_for(search, 1)
    finally:
        search.cancel()
        await asyncio.gather(search, return_exceptions=True)
    assert result == {"ok": True}
    assert isinstance(fs.image_url, str)
    prefix, data = fs.image_url.split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(data) == b"\x89PNG\r\n\x1a\n"
    assert fs.retry_values == {"find": 0, "search": 3}

    await service.search(query="search", ctx=ctx)

    assert fs.retry_values == {"find": 0, "search": 0}
    assert effective_max_retries(3) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["search", "find", "find_skills"])
async def test_search_operations_reject_system_target_before_vector_access(operation):
    fs = FakeVikingFS()
    fs.search = AsyncMock(side_effect=AssertionError("system target must not reach vector search"))
    fs.find = AsyncMock(side_effect=AssertionError("system target must not reach vector find"))
    service = SearchService(fs)
    ctx = RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)
    target_uri = "viking://system/memory/memory_templates/custom"

    with pytest.raises(InvalidArgumentError, match="search is not supported"):
        await getattr(service, operation)(query="demo", target_uri=target_uri, ctx=ctx)

    fs.search.assert_not_awaited()
    fs.find.assert_not_awaited()
