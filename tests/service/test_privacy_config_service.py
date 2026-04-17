# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.utils.privacy_config_service import PrivacyConfigService
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.directories: set[str] = set()

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None, **kwargs):
        del exist_ok, ctx, kwargs
        self.directories.add(uri.rstrip("/"))

    async def write_file(self, uri: str, content: str, ctx=None):
        del ctx
        self.files[uri] = content

    async def write_file_bytes(self, uri: str, content: bytes, ctx=None):
        del ctx
        self.files[uri] = content.decode("utf-8")

    async def write_context(
        self,
        uri: str,
        content: str = "",
        abstract: str = "",
        overview: str = "",
        content_filename: str = "content.md",
        is_leaf: bool = False,
        ctx=None,
    ):
        del is_leaf, ctx
        self.directories.add(uri.rstrip("/"))
        if content:
            self.files[f"{uri}/{content_filename}"] = content
        if abstract:
            self.files[f"{uri}/.abstract.md"] = abstract
        if overview:
            self.files[f"{uri}/.overview.md"] = overview

    async def read_file(self, uri: str, offset: int = 0, limit: int = -1, ctx=None) -> str:
        del ctx
        text = self.files[uri]
        if offset == 0 and limit == -1:
            return text
        lines = text.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)


def _request_context() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)


def _skill_markdown(name: str, body: str) -> str:
    return f"""---
name: {name}
description: test skill
---

{body}
"""


async def test_skill_privacy_config_versions_persist(monkeypatch):
    fake_fs = FakeVikingFS()
    privacy_service = PrivacyConfigService(viking_fs=fake_fs)
    processor = SkillProcessor(vikingdb=SimpleNamespace(), privacy_config_service=privacy_service)
    ctx = _request_context()

    monkeypatch.setattr(
        "openviking.utils.skill_processor.get_openviking_config",
        lambda: SimpleNamespace(vlm=SimpleNamespace()),
    )
    processor._generate_overview = AsyncMock(return_value="redacted overview")
    processor._index_skill = AsyncMock(return_value=None)

    await processor.process_skill(
        _skill_markdown("privacy-versioned-skill", 'api_key: "alpha-secret"\n'),
        fake_fs,
        ctx,
    )
    await processor.process_skill(
        _skill_markdown("privacy-versioned-skill", 'api_key: "beta-secret"\ntoken: beta-token\n'),
        fake_fs,
        ctx,
    )

    skill_md_uri = "viking://agent/skills/privacy-versioned-skill/SKILL.md"
    storage = privacy_service.describe_storage("skill", skill_md_uri, ctx)

    current = json.loads(fake_fs.files[storage["current_uri"]])
    version_001 = json.loads(fake_fs.files[f"{storage['history_uri']}/version_001.json"])
    version_002 = json.loads(fake_fs.files[f"{storage['history_uri']}/version_002.json"])
    meta = json.loads(fake_fs.files[storage["meta_uri"]])

    assert current["version"] == 2
    assert current["secrets"]["api_key"] == "beta-secret"
    assert current["secrets"]["token"] == "beta-token"
    assert version_001["secrets"]["api_key"] == "alpha-secret"
    assert version_002["secrets"]["api_key"] == "beta-secret"
    assert meta["current_version"] == 2
    assert meta["secret_fields"] == ["api_key", "token"]


async def test_skill_read_restores_user_secret_from_privacy_config(monkeypatch):
    fake_fs = FakeVikingFS()
    privacy_service = PrivacyConfigService(viking_fs=fake_fs)
    processor = SkillProcessor(vikingdb=SimpleNamespace(), privacy_config_service=privacy_service)
    fs_service = FSService(viking_fs=fake_fs, privacy_config_service=privacy_service)
    ctx = _request_context()

    monkeypatch.setattr(
        "openviking.utils.skill_processor.get_openviking_config",
        lambda: SimpleNamespace(vlm=SimpleNamespace()),
    )
    processor._generate_overview = AsyncMock(return_value="redacted overview")
    processor._index_skill = AsyncMock(return_value=None)

    result = await processor.process_skill(
        _skill_markdown(
            "privacy-restored-skill",
            '# Skill\napi_key: "super-secret"\nbase_url: https://internal.example.com\n',
        ),
        fake_fs,
        ctx,
    )
    skill_md_uri = f"{result['uri']}/SKILL.md"

    raw_stored = await fake_fs.read_file(skill_md_uri, ctx=ctx)
    restored = await fs_service.read(skill_md_uri, ctx=ctx)

    assert "super-secret" not in raw_stored
    assert "internal.example.com" not in raw_stored
    assert "{{OV_PRIVACY:api_key}}" in raw_stored
    assert "{{OV_PRIVACY:base_url}}" in raw_stored

    assert 'api_key: "super-secret"' in restored
    assert "base_url: https://internal.example.com" in restored
    assert "{{OV_PRIVACY:" not in restored
