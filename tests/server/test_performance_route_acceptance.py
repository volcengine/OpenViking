"""Additive route acceptance through the real Console router."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.console import router
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_console_exposes_disabled_performance_capability():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("synthetic", "alice"), role=Role.USER
    )
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/console/performance",
            params={
                "start": (now - timedelta(hours=1)).isoformat(),
                "end": now.isoformat(),
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["enabled"] is False
