"""HTTP download limits reach the existing authorized, physically bounded read."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openviking.server.auth import get_download_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.routers import content
from openviking.service.fs_service import FSService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS

URI = "viking://resources/demo/input.bin"
CTX = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


class SizedFilesystem(MockLocalAGFS):
    def __init__(self, root):
        super().__init__(root_path=root)
        self.read_sizes = []
        self.read_bytes = 0
        self.grow_after_stat = None

    def read(self, path, ctx=None, **kwargs):
        size = kwargs.get("size", -1)
        self.read_sizes.append(size)
        with self._resolve(path).open("rb") as stream:
            data = stream.read(size)
        self.read_bytes += len(data)
        return data

    def stat(self, path, ctx=None):
        result = super().stat(path, ctx=ctx)
        if self.grow_after_stat is not None and path.endswith("input.bin"):
            self._resolve(path).write_bytes(self.grow_after_stat)
            self.grow_after_stat = None
        return result


@pytest.fixture
async def bounded_api(tmp_path, monkeypatch):
    backend = SizedFilesystem(tmp_path)
    local = tmp_path / "local/default/resources/demo/input.bin"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"synthetic")
    service = SimpleNamespace(fs=FSService(viking_fs=VikingFS(agfs=backend)))
    monkeypatch.setattr(content, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(content.router)
    app.dependency_overrides[get_download_request_context] = lambda: CTX

    @app.exception_handler(OpenVikingError)
    async def errors(_request, error):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[error.code], content={"error": error.code}
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, backend, local


async def test_oversized_http_download_rejects_before_physical_read(bounded_api):
    client, backend, _ = bounded_api
    response = await client.get("/api/v1/content/download", params={"uri": URI, "max_bytes": 2})
    assert response.status_code == ERROR_CODE_TO_HTTP_STATUS["RESOURCE_EXHAUSTED"], response.text
    assert backend.read_sizes == []


async def test_growth_after_stat_uses_limit_plus_one_physical_read(bounded_api):
    client, backend, local = bounded_api
    local.write_bytes(b"a")
    backend.grow_after_stat = b"0123456789"
    response = await client.get("/api/v1/content/download", params={"uri": URI, "max_bytes": 2})
    assert response.status_code == ERROR_CODE_TO_HTTP_STATUS["RESOURCE_EXHAUSTED"], response.text
    assert backend.read_sizes == [3]
    assert backend.read_bytes == 3


@pytest.mark.parametrize("data,limit", [(b"", 0), (b"abcd", 4)])
async def test_exact_limit_and_empty_http_files(bounded_api, data, limit):
    client, backend, local = bounded_api
    local.write_bytes(data)
    response = await client.get("/api/v1/content/download", params={"uri": URI, "max_bytes": limit})
    assert response.status_code == 200 and response.content == data
    assert backend.read_sizes == [limit + 1]


async def test_omitted_limit_preserves_existing_http_behavior(bounded_api):
    client, backend, _ = bounded_api
    response = await client.get("/api/v1/content/download", params={"uri": URI})
    assert response.status_code == 200 and response.content == b"synthetic"
    assert backend.read_sizes == [-1]


@pytest.mark.parametrize("limit", [-1, "invalid", 2**63 - 1])
async def test_invalid_limit_rejects_before_read(bounded_api, limit):
    client, backend, _ = bounded_api
    response = await client.get("/api/v1/content/download", params={"uri": URI, "max_bytes": limit})
    assert response.status_code == 422
    assert backend.read_sizes == []
