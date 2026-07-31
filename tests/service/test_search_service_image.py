import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self):
        self.image_url = None

    async def read_file_bytes(self, uri, ctx=None):
        assert uri == "viking://resources/cat.png"
        return b"\x89PNG\r\n\x1a\n"

    async def find(self, **kwargs):
        self.image_url = kwargs["image_url"]
        return {"ok": True}


@pytest.mark.asyncio
async def test_search_service_resolves_viking_image_url_to_data_uri():
    fs = FakeVikingFS()
    service = SearchService(fs)
    ctx = RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)

    result = await service.find(
        query="",
        image_url="viking://resources/cat.png",
        ctx=ctx,
    )

    assert result == {"ok": True}
    assert fs.image_url.startswith("data:image/png;base64,")
