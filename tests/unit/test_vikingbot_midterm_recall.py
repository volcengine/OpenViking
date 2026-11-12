# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.memory import MemoryStore
from vikingbot.openviking_mount.ov_server import VikingClient


class _FakeVikingClient:
    actor_peer_id = None

    def __init__(self):
        self.targets: list[str] = []

    def build_current_memory_target_uris(self, **_kwargs):
        return ["viking://user/default/peers/conv-26/memories/"]

    async def find(self, **kwargs):
        target = kwargs["target_uri"]
        self.targets.append(target)
        if target.endswith("/midterm_qa/"):
            return [
                {
                    "uri": f"{target}pair_hash.md",
                    "score": 0.91,
                    "abstract": "Caroline attended on 7 May 2023.",
                }
            ]
        return []

    async def read_content(self, uri, level="read"):
        del uri, level
        return "# Dialogue Pair\n\nCaroline: I attended on 7 May 2023."

    async def close(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_midterm_vector_recall_has_independent_quota_and_group(
    tmp_path, monkeypatch, enabled
):
    ov_server = SimpleNamespace(
        admin_user_id="default",
        memory_recall_events_limit=1,
        memory_recall_entities_limit=1,
        memory_recall_preferences_limit=1,
        memory_recall_max_chars=1000,
        memory_recall_midterm_limit=7,
        memory_recall_midterm_max_chars=1200,
        memory_recall_midterm_min_score=0.25,
    )
    store = MemoryStore(
        tmp_path, config=SimpleNamespace(ov_server=ov_server, _topic_continuity_enabled=enabled)
    )
    client = _FakeVikingClient()
    monkeypatch.setattr(VikingClient, "create", AsyncMock(return_value=client))

    context = await store.get_viking_memory_context(
        current_message="When did Caroline attend?",
        workspace_id="agent",
        sender_id="conv-26",
    )

    assert any(target.endswith("/midterm_qa/") for target in client.targets) is enabled
    if not enabled:
        assert context == ""
        return
    assert '<memory_group type="midterm_qa">' in context
    assert "7 May 2023" in context


@pytest.mark.parametrize("enabled", [None, False, True])
def test_bot_inherits_single_root_switch(tmp_path, monkeypatch, enabled):
    import json

    from vikingbot.config import loader

    from openviking_cli.utils.config.memory_config import MemoryConfig

    memory = {} if enabled is None else {"topic_continuity": {"enabled": enabled}}
    path = tmp_path / "ov.conf"
    path.write_text(
        json.dumps(
            {"memory": memory, "bot": {"ov_server": {"memory_recall_midterm_enabled": True}}}
        )
    )
    monkeypatch.setattr(loader, "CONFIG_PATH", path)
    config = loader.load_config()
    assert (
        config._topic_continuity_enabled
        is MemoryConfig.model_validate(memory).topic_continuity.enabled
    )
    assert config._topic_continuity_enabled is (enabled is True)
    assert "memory_recall_midterm_enabled" not in config.ov_server.model_dump()
    assert "_topic_continuity_enabled" not in config.model_dump()
