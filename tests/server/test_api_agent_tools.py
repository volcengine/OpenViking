# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for agent-facing remember endpoint."""

import json
from unittest.mock import patch

import httpx
import pytest

from openviking.server.agent_tools import RememberRequest, remember
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture(autouse=True)
def _configure_test_env(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local", "mode": "binding-client"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test-embedder",
                        "api_base": "http://127.0.0.1:11434/v1",
                        "dimension": 1024,
                    }
                },
                "encryption": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    OpenVikingConfigSingleton.reset_instance()

    with patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs):
        yield

    OpenVikingConfigSingleton.reset_instance()


async def test_remember_text_creates_temp_session(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/agent/remember",
        json={"text": "Remember that my preferred editor is Vim."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["session_id"].startswith("agent-remember-")
    assert result["message_count"] == 1
    assert result["used_temp_session"] is True
    assert result["status"] == "accepted"

    session_resp = await client.get(f"/api/v1/sessions/{result['session_id']}")
    assert session_resp.status_code == 200


async def test_remember_messages_preserves_role_id_and_created_at(
    client: httpx.AsyncClient,
):
    resp = await client.post(
        "/api/v1/agent/remember",
        json={
            "session_id": "remember-explicit-session",
            "messages": [
                {
                    "role": "user",
                    "content": "Remember this role scoped fact.",
                    "role_id": "wx_user-01_abc",
                    "created_at": "2026-05-06T01:02:03Z",
                }
            ],
        },
    )

    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["session_id"] == "remember-explicit-session"
    assert result["used_temp_session"] is False

    archive_resp = await client.get(
        "/api/v1/sessions/remember-explicit-session/archives/archive_001"
    )
    assert archive_resp.status_code == 200
    messages = archive_resp.json()["result"]["messages"]
    assert messages[0]["role_id"] == "wx_user-01_abc"
    assert messages[0]["created_at"] == "2026-05-06T01:02:03Z"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": "hello", "messages": [{"role": "user", "content": "hello"}]},
        {"text": "hello", "cleanup_session": True},
    ],
)
async def test_remember_rejects_invalid_payloads(
    client: httpx.AsyncClient,
    payload: dict,
):
    resp = await client.post("/api/v1/agent/remember", json=payload)

    assert resp.status_code == 400


async def test_remember_cleanup_failure_does_not_fail_user_call(service):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.USER,
    )

    result = await remember(
        service,
        ctx,
        RememberRequest(
            text="Remember that cleanup failure should not mask store success.",
            wait=True,
            cleanup_session=True,
            timeout_ms=1000,
        ),
    )

    assert result["message_count"] == 1
    assert result["cleaned_up"] is False
    assert "cleanup_error" in result
