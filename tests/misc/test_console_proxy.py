import httpx
import pytest

from openviking.console.app import create_console_app
from openviking.console.config import ConsoleConfig


@pytest.mark.asyncio
async def test_console_bff_proxy_forwards_console_routes():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/console/dashboard/summary"
        assert request.url.query == b"range=today"
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            status_code=200,
            json={"status": "ok", "result": {"enabled": True}},
            headers={"x-request-id": "req-console"},
        )

    app = create_console_app(
        config=ConsoleConfig(openviking_base_url="http://openviking.test"),
        upstream_transport=httpx.MockTransport(handler),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://console.test") as client:
        response = await client.get(
            "/console/api/v1/ov/console/dashboard/summary?range=today",
            headers={"x-api-key": "test-key"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-console"
    assert response.json() == {"status": "ok", "result": {"enabled": True}}


@pytest.mark.asyncio
async def test_console_bff_proxy_rejects_path_traversal():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(status_code=500)

    app = create_console_app(
        config=ConsoleConfig(openviking_base_url="http://openviking.test"),
        upstream_transport=httpx.MockTransport(handler),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://console.test") as client:
        response = await client.get("/console/api/v1/ov/console/%2e%2e/admin/accounts")

    assert response.status_code == 404
    assert called is False
