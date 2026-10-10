"""Authentication covers the whole public adapter, before routing or body parsing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from benchmark.aml.server import AMLSettings, create_app


@pytest.fixture
def protected_adapter():
    backend = SimpleNamespace(
        ready=AsyncMock(return_value=True),
        find=AsyncMock(return_value=[]),
        add_and_commit=AsyncMock(),
    )
    app = create_app(settings=AMLSettings(aml_api_key="test-key"), backend=backend)
    return app, backend


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-key"}])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/"),
        ("GET", "/health"),
        ("GET", "/docs"),
        ("GET", "/redoc"),
        ("GET", "/openapi.json"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/unknown"),
        ("GET", "/health/"),
        ("HEAD", "/docs"),
        ("OPTIONS", "/search"),
        ("POST", "/add"),
        ("POST", "/search"),
    ],
)
async def test_all_paths_reject_missing_or_invalid_key(protected_adapter, method, path, headers):
    app, backend = protected_adapter
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        response = await client.request(method, path, headers=headers, content=b"invalid json")
    assert response.status_code == 401
    backend.ready.assert_not_called()
    backend.find.assert_not_called()
    backend.add_and_commit.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer test-key"},
        {"Authorization": "Token test-key"},
        {"X-API-Key": "test-key"},
    ],
)
async def test_valid_key_preserves_endpoints_and_routing(protected_adapter, headers):
    app, backend = protected_adapter
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test", headers=headers
    ) as client:
        for path in ["/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]:
            assert (await client.get(path)).status_code == 200
        assert (await client.get("/")).status_code == 404
        assert (await client.get("/health/")).status_code == 307
        assert (await client.post("/add", json={})).status_code == 422
        search = await client.post("/search", json={"query": "Where?", "user_id": "u", "top_k": 1})
        assert search.status_code == 200
        assert search.json() == {"data": []}
        add = await client.post(
            "/add",
            json={
                "request_id": "r",
                "user_id": "u",
                "session_id": "s",
                "messages": [{"role": "user", "content": "Test message"}],
            },
        )
        assert add.status_code == 200
    backend.find.assert_awaited_once_with(user_id="u", query="Where?", limit=1)
    backend.add_and_commit.assert_awaited_once()


async def test_local_no_key_configuration_remains_optional():
    app = create_app(
        settings=AMLSettings(), backend=SimpleNamespace(ready=AsyncMock(return_value=True))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        assert (await client.get("/health")).status_code == 200
