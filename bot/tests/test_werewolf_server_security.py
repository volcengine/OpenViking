# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from fastapi.testclient import TestClient

from bot.demo.werewolf.werewolf_server import GameState, create_fastapi_app


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
