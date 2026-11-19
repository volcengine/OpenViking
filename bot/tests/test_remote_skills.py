# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Remote Skill activation integrity tests."""

import hashlib

import pytest
from vikingbot.agent.remote_skills import SkillRuntimeContext
from vikingbot.config.schema import Config, SessionKey


@pytest.mark.asyncio
async def test_activation_accepts_privacy_restored_skill_content(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:privacy-demo:api_key}}"
    stored_content = f"""---
name: privacy-demo
description: Privacy restoration regression fixture
---
Use {placeholder}.
"""
    restored_content = stored_content.replace(placeholder, "test-value")
    root_uri = "viking://user/demo/skills/privacy-demo"
    skill_md_uri = f"{root_uri}/SKILL.md"

    class FakeClient:
        def __init__(self):
            self.downloaded = []

        async def get_skill(self, *args, **kwargs):
            return {
                "root_uri": root_uri,
                "content": restored_content,
                "content_sha256": hashlib.sha256(restored_content.encode()).hexdigest(),
                "revision": "fixture-revision",
                "files": [
                    {
                        "path": "SKILL.md",
                        "uri": skill_md_uri,
                        "is_dir": False,
                        "kind": "definition",
                        "size": len(stored_content.encode()),
                        "sha256": hashlib.sha256(stored_content.encode()).hexdigest(),
                    }
                ],
            }

        async def download_bytes(self, uri):
            self.downloaded.append(uri)
            return stored_content.encode()

    client = FakeClient()
    runtime = SkillRuntimeContext(
        config=Config(storage_workspace=str(tmp_path), hooks=[]),
        session_key=SessionKey(type="cli", channel_id="default", chat_id="privacy-skill"),
        sandbox_manager=None,
        workspace_id="workspace",
        openviking_connection=None,
        actor_peer_id=None,
    )

    async def get_client():
        return client

    monkeypatch.setattr(runtime, "_get_client", get_client)

    active = await runtime.activate_from_read(skill_md_uri, stored_content)

    assert active is not None
    assert active.content == restored_content
    assert active.files["SKILL.md"].sha256 == hashlib.sha256(stored_content.encode()).hexdigest()
    assert client.downloaded == [skill_md_uri]
