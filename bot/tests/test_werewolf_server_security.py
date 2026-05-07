# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from bot.demo.werewolf.werewolf_server import GameState, create_fastapi_app


def _make_client(tmp_path) -> TestClient:
    state = GameState(
        game_id="test-game",
        vikingbot_url="http://127.0.0.1:18790",
        config_path=tmp_path / "ov.conf",
        config={},
        storage_path=tmp_path,
    )
    return TestClient(create_fastapi_app(state))


def test_start_endpoint_hides_value_error_details(tmp_path):
    client = _make_client(tmp_path)

    async def _raise_value_error(_state):
        raise ValueError("secret filesystem detail")

    with patch("bot.demo.werewolf.werewolf_server.start_current_game", new=_raise_value_error):
        response = client.post("/api/start", json={"game_mode": "all_agents"})

    assert response.status_code == 200
    assert response.json() == {"success": False, "error": "Failed to start game"}
    assert "secret filesystem detail" not in response.text


def test_conversation_endpoint_hides_internal_exception_details(tmp_path):
    conversation_dir = tmp_path / "bot" / "workspace" / "werewolf"
    conversation_dir.mkdir(parents=True, exist_ok=True)
    (conversation_dir / "CONVERSATION_demo.md").write_text("demo", encoding="utf-8")

    client = _make_client(tmp_path)

    with patch.object(Path, "read_text", side_effect=RuntimeError("secret stack detail")):
        response = client.get("/api/conversation/demo")

    assert response.status_code == 500
    assert response.json() == {"error": "Failed to read conversation"}
    assert "secret stack detail" not in response.text


def test_openviking_file_rejects_path_traversal(tmp_path):
    workspace = tmp_path / "level1" / "level2" / "workspace"
    (workspace / "viking" / "default").mkdir(parents=True, exist_ok=True)
    secret_file = workspace.parent / "secret.txt"
    secret_file.write_text("TOPSECRET", encoding="utf-8")

    state = GameState(
        game_id="test-game",
        vikingbot_url="http://127.0.0.1:18790",
        config_path=tmp_path / "ov.conf",
        config={"storage": {"workspace": str(workspace)}},
        storage_path=workspace,
    )
    client = TestClient(create_fastapi_app(state))

    response = client.get("/api/openviking/file", params={"path": "../../../secret.txt"})

    assert response.status_code == 404
    assert response.json() == {"error": "File not found"}
    assert "TOPSECRET" not in response.text
