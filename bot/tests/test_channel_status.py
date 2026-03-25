# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for GET /bot/v1/channels/status endpoint."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig


def _make_channel(
    app: FastAPI,
    *,
    api_key: str = "",
    status_provider=None,
) -> OpenAPIChannel:
    config = OpenAPIChannelConfig(enabled=True, api_key=api_key)
    channel = OpenAPIChannel(config, MessageBus(), app=app, status_provider=status_provider)
    channel._setup_routes()
    return channel


def _make_app() -> FastAPI:
    return FastAPI()


def _fake_status():
    return {
        "openapi": {"enabled": True, "running": True},
        "telegram": {"enabled": True, "running": False},
    }


@pytest.fixture()
def app_with_status():
    app = _make_app()
    _make_channel(app, status_provider=_fake_status)
    return app


@pytest.fixture()
def app_without_status():
    app = _make_app()
    _make_channel(app, status_provider=None)
    return app


@pytest.fixture()
def app_with_auth():
    app = _make_app()
    _make_channel(app, api_key="test-secret", status_provider=_fake_status)
    return app


class TestChannelStatusEndpoint:
    """GET /bot/v1/channels/status tests."""

    async def test_returns_channel_status(self, app_with_status):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_status), base_url="http://test"
        ) as ac:
            resp = await ac.get("/bot/v1/channels/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["result"]["openapi"]["running"] is True
        assert body["result"]["telegram"]["running"] is False

    async def test_returns_503_when_provider_missing(self, app_without_status):
        async with AsyncClient(
            transport=ASGITransport(app=app_without_status), base_url="http://test"
        ) as ac:
            resp = await ac.get("/bot/v1/channels/status")

        assert resp.status_code == 503

    async def test_requires_api_key_when_configured(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as ac:
            # No key -> 401
            resp = await ac.get("/bot/v1/channels/status")
            assert resp.status_code == 401

            # Wrong key -> 403
            resp = await ac.get(
                "/bot/v1/channels/status",
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 403

            # Correct key -> 200
            resp = await ac.get(
                "/bot/v1/channels/status",
                headers={"X-API-Key": "test-secret"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
