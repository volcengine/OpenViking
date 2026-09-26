# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.routers import resources
from openviking_cli.utils.config import (
    DingTalkConfig,
    DingTalkIdentityConfig,
    DingTalkMCPServerConfig,
)


async def test_dingtalk_identity_options_are_authenticated_and_hide_secrets(monkeypatch):
    app = FastAPI()
    app.include_router(resources.router)
    auth_calls = []

    def authenticated_context():
        auth_calls.append(True)
        return SimpleNamespace()

    app.dependency_overrides[get_request_context] = authenticated_context
    config = DingTalkConfig(
        identities={
            "team-docs": DingTalkIdentityConfig(
                label="Team documentation",
                doc=DingTalkMCPServerConfig(
                    url="https://mcp.example.test/private",
                    headers={"Authorization": "Bearer secret-value"},
                ),
            )
        }
    )
    monkeypatch.setattr(
        resources,
        "get_openviking_config",
        lambda: SimpleNamespace(dingtalk=config),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/resources/dingtalk/identities")

    assert response.status_code == 200
    assert auth_calls == [True]
    assert response.json() == {
        "status": "ok",
        "result": [{"name": "team-docs", "label": "Team documentation"}],
    }
    assert "mcp.example.test" not in response.text
    assert "secret-value" not in response.text


async def test_dingtalk_identity_options_can_be_empty(monkeypatch):
    app = FastAPI()
    app.include_router(resources.router)
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace()
    monkeypatch.setattr(
        resources,
        "get_openviking_config",
        lambda: SimpleNamespace(dingtalk=DingTalkConfig()),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/resources/dingtalk/identities")

    assert response.status_code == 200
    assert response.json()["result"] == []
