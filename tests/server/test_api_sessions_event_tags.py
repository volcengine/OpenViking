# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx
import pytest

from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture(autouse=True)
def _configure_test_env(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local"},
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
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    OpenVikingConfigSingleton.reset_instance()
    yield
    OpenVikingConfigSingleton.reset_instance()


def _event_tags(response: httpx.Response) -> list[str]:
    return response.json()["result"]["memory_extraction_config"]["events"]["tags"]


async def test_session_config_updates_event_tags_and_auto_commit_policy(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {
                "events": {"tags": [" Team=Search ", "team=search", "channel=web"]}
            },
            "auto_commit_policy": {
                "pending_token_threshold": 8000,
                "message_count_threshold": 40,
            },
        },
    )
    assert created.status_code == 200
    assert _event_tags(created) == ["team=search", "channel=web"]
    session_id = created.json()["result"]["session_id"]

    fetched = await client.get(f"/api/v1/sessions/{session_id}")
    assert _event_tags(fetched) == ["team=search", "channel=web"]
    assert "event_search_tags" not in fetched.json()["result"]

    updated = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={
            "memory_extraction_config": {"events": {"tags": ["channel=app"]}},
            "auto_commit_policy": {"message_count_threshold": 25},
        },
    )
    result = updated.json()["result"]
    assert updated.status_code == 200
    assert _event_tags(updated) == ["channel=app"]
    assert result["auto_commit_idle_enabled"] is False
    assert result["auto_commit_policy"]["pending_token_threshold"] == 8000
    assert result["auto_commit_policy"]["message_count_threshold"] == 25

    cleared = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"memory_extraction_config": {"events": {"tags": []}}},
    )
    assert _event_tags(cleared) == []
    assert cleared.json()["result"]["auto_commit_policy"]["message_count_threshold"] == 25

    disabled = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"auto_commit_policy": None},
    )
    assert disabled.status_code == 200
    assert disabled.json()["result"]["auto_commit_policy"] is None


async def test_session_event_tags_discard_invalid_values(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {
                "events": {"tags": ["missing-equals", "channel=web"]}
            }
        },
    )

    assert response.status_code == 200
    assert _event_tags(response) == ["channel=web"]


async def test_create_session_duplicate_event_tag_keys_keep_last_value(
    client: httpx.AsyncClient,
):
    response = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {
                "events": {"tags": ["channel=web", "channel=app"]}
            }
        },
    )

    assert response.status_code == 200
    assert _event_tags(response) == ["channel=app"]


async def test_patch_session_config_duplicate_event_tag_keys_keep_last_value(
    client: httpx.AsyncClient,
):
    created = await client.post("/api/v1/sessions", json={})
    session_id = created.json()["result"]["session_id"]

    response = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={
            "memory_extraction_config": {
                "events": {"tags": ["channel=web", "channel=app"]}
            }
        },
    )

    assert response.status_code == 200
    assert _event_tags(response) == ["channel=app"]


async def test_commit_duplicate_event_tag_keys_keep_last_value(client: httpx.AsyncClient):
    created = await client.post("/api/v1/sessions", json={})
    session_id = created.json()["result"]["session_id"]
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want a refund"},
    )

    response = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        json={
            "extraction_metadata": {
                "event": {"tags": ["channel=web", "channel=app"]}
            }
        },
    )

    assert response.status_code == 200
