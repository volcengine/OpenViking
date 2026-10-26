import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking.utils.model_retry import effective_max_retries
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self):
        self.image_url = None
        self.retry_values = {}

    async def read_file_bytes(self, uri, ctx=None):
        assert uri == "viking://resources/cat.png"
        return b"\x89PNG\r\n\x1a\n"

    async def find(self, **kwargs):
        self.image_url = kwargs["image_url"]
        await asyncio.sleep(0)  # Keep the search scope active during the concurrent call.
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

    result, _ = await asyncio.gather(
        service.find(query="", image_url="viking://resources/cat.png", ctx=ctx),
        fs.search(query="concurrent background work"),
    )

    assert result == {"ok": True}
    assert fs.image_url.startswith("data:image/png;base64,")
    assert fs.retry_values == {"find": 0, "search": 3}

    await service.search(query="search", ctx=ctx)

    assert fs.retry_values == {"find": 0, "search": 0}
    assert effective_max_retries(3) == 3
