# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.core.namespace import canonical_user_root
from openviking.privacy.skill_extractor import extract_skill_privacy_values
from openviking.privacy.skill_placeholder import placeholderize_skill_content_with_blocks
from openviking.privacy.service import UserPrivacyConfigService
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _MemoryPrivacyStorage:
    def __init__(self):
        self.files: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._async_agfs = self
        self.lock_timeouts = []

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_tree(self, _path, timeout_secs=0.0):
        self.lock_timeouts.append(timeout_secs)
        if timeout_secs == 0.0:
            acquired = self._lock.locked() is False and await self._lock.acquire()
        else:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout_secs)
                acquired = True
            except asyncio.TimeoutError:
                acquired = False
        if not acquired:
            raise RuntimeError("lock acquire timed out")
        return {"lease_ref": "test"}

    async def pathlock_release(self, _lease):
        self._lock.release()

    async def mkdir(self, *_args, **_kwargs):
        await asyncio.sleep(0)

    async def read_file(self, uri, **_kwargs):
        await asyncio.sleep(0)
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def write_file(self, uri, content, **_kwargs):
        await asyncio.sleep(0)
        self.files[uri] = content

    async def ls(self, uri, **_kwargs):
        await asyncio.sleep(0)
        prefix = f"{uri}/"
        return [
            {"name": path.removeprefix(prefix)}
            for path in self.files
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]


@pytest.mark.asyncio
async def test_privacy_config_service_versions(service):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_user"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)

    privacy = service.privacy_configs
    first = await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="demo-skill",
        values={"api_key": "secret-1"},
        updated_by=ctx.user.user_id,
    )
    same = await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="demo-skill",
        values={"api_key": "secret-1"},
        updated_by=ctx.user.user_id,
    )
    second = await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="demo-skill",
        values={"api_key": "secret-2"},
        updated_by=ctx.user.user_id,
    )

    assert first.version == 1
    assert same.version == 1
    assert second.version == 2
    assert await privacy.list_versions(ctx, "skill", "demo-skill") == [1, 2]

    restored = await privacy.activate_version(
        ctx, "skill", "demo-skill", 1, updated_by=ctx.user.user_id
    )
    current = await privacy.get_current(ctx, "skill", "demo-skill")
    assert restored.version == 1
    assert current.version == 1
    assert current.values["api_key"] == "secret-1"


@pytest.mark.asyncio
async def test_privacy_config_concurrent_updates_keep_every_version():
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_race"), role=Role.ROOT)
    privacy = UserPrivacyConfigService(_MemoryPrivacyStorage())
    results = await asyncio.gather(
        *(
            privacy.upsert(
                ctx=ctx,
                category="skill",
                target_key="concurrent-skill",
                values={"api_key": f"secret-{index}"},
                updated_by=ctx.user.user_id,
            )
            for index in range(8)
        )
    )

    assert sorted(result.version for result in results) == list(range(1, 9))
    assert await privacy.list_versions(ctx, "skill", "concurrent-skill") == list(range(1, 9))


@pytest.mark.asyncio
async def test_privacy_config_exists_propagates_storage_errors():
    class FailingStorage:
        async def stat(self, *_args, **_kwargs):
            raise RuntimeError("storage unavailable")

    privacy = UserPrivacyConfigService(FailingStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_error"), role=Role.ROOT)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await privacy.exists(ctx, "skill", "demo-skill")


@pytest.mark.asyncio
@pytest.mark.parametrize("read_method", ["read_file", "read_file_bytes"])
async def test_viking_fs_read_propagates_storage_errors(read_method):
    class FailingReadStorage:
        def stat(self, _path, **_kwargs):
            return {"isDir": False}

        def read(self, _path, **_kwargs):
            raise RuntimeError("storage unavailable")

    fs = VikingFS(FailingReadStorage())
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_read_error"), role=Role.ROOT)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await getattr(fs, read_method)("viking://user/privacy/meta.json", ctx=ctx)


@pytest.mark.asyncio
async def test_skill_read_restores_placeholder(service):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_restore"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)

    await service.resources.add_skill(
        data={
            "name": "restore-skill",
            "description": "skill with secret",
            "content": 'api_key: "secret-xyz"\nbase_url: "https://example.com"\n',
        },
        ctx=ctx,
        wait=False,
    )

    skill_file_uri = f"{canonical_user_root(ctx)}/skills/restore-skill/SKILL.md"
    stored = await service.viking_fs.read_file(skill_file_uri, ctx=ctx)
    restored = await service.fs.read(skill_file_uri, ctx=ctx)

    assert "secret-xyz" not in stored
    assert "{{ov_privacy:skill:restore-skill:api_key}}" in stored
    assert "secret-xyz" in restored
    assert "https://example.com" in restored


@pytest.mark.asyncio
async def test_skill_read_restores_placeholder_for_agent_segment_name(service):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("privacy_restore_agent_segment"), role=Role.ROOT
    )
    await service.initialize_user_directories(ctx)

    await service.resources.add_skill(
        data={
            "name": "restore-skill-agent-segment",
            "description": "skill with secret",
            "content": 'api_key: "secret-abc"\nbase_url: "https://example.com"\n',
        },
        ctx=ctx,
        wait=False,
    )

    restored = await service.fs.read(
        f"{canonical_user_root(ctx)}/skills/restore-skill-agent-segment/SKILL.md",
        ctx=ctx,
    )

    assert "secret-abc" in restored
    assert "https://example.com" in restored


@pytest.mark.asyncio
async def test_skill_privacy_extraction_returns_content_blocks():
    content = 'api_key: "secret-xyz"\nbase_url: "https://example.com"\n'
    result = await extract_skill_privacy_values(
        skill_name="extract-block-skill",
        skill_description="skill with secret",
        content=content,
    )

    assert result.values["api_key"] == "secret-xyz"
    assert result.values["base_url"] == "https://example.com"
    assert result.original_content == content
    assert result.original_content_blocks == ["https://example.com", "secret-xyz"]
    assert result.replacement_content_blocks == [
        "{{ov_privacy:skill:extract-block-skill:base_url}}",
        "{{ov_privacy:skill:extract-block-skill:api_key}}",
    ]
    assert result.sanitized_content == (
        'api_key: "{{ov_privacy:skill:extract-block-skill:api_key}}"\n'
        'base_url: "{{ov_privacy:skill:extract-block-skill:base_url}}"\n'
    )


@pytest.mark.asyncio
async def test_skill_read_appends_notice_for_unreplaced_placeholders(service):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("privacy_partial_restore"), role=Role.ROOT
    )
    await service.initialize_user_directories(ctx)

    skill_uri = f"{canonical_user_root(ctx)}/skills/partial-restore-skill"
    await service.viking_fs.mkdir(skill_uri, exist_ok=True, ctx=ctx)
    await service.viking_fs.write_file(
        f"{skill_uri}/SKILL.md",
        (
            'api_key: "{{ov_privacy:skill:partial-restore-skill:api_key}}"\n'
            'region: "{{ov_privacy:skill:partial-restore-skill:region}}"\n'
        ),
        ctx=ctx,
    )

    await service.privacy_configs.upsert(
        ctx=ctx,
        category="skill",
        target_key="partial-restore-skill",
        values={"api_key": "secret-xyz"},
        updated_by=ctx.user.user_id,
    )

    restored = await service.fs.read(f"{skill_uri}/SKILL.md", ctx=ctx)

    assert 'api_key: "secret-xyz"' in restored
    assert "{{ov_privacy:skill:partial-restore-skill:region}}" in restored
    assert "[Privacy Config Notice]" in restored
    assert "Missing config: region=<missing>" in restored


@pytest.mark.asyncio
async def test_skill_read_appends_notice_for_extra_configured_keys(service):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("privacy_extra_config"), role=Role.ROOT
    )
    await service.initialize_user_directories(ctx)

    skill_uri = f"{canonical_user_root(ctx)}/skills/extra-config-skill"
    await service.viking_fs.mkdir(skill_uri, exist_ok=True, ctx=ctx)
    await service.viking_fs.write_file(
        f"{skill_uri}/SKILL.md",
        'api_key: "{{ov_privacy:skill:extra-config-skill:api_key}}"\n',
        ctx=ctx,
    )

    await service.privacy_configs.upsert(
        ctx=ctx,
        category="skill",
        target_key="extra-config-skill",
        values={"api_key": "secret-xyz", "region": "cn"},
        updated_by=ctx.user.user_id,
    )

    restored = await service.fs.read(f"{skill_uri}/SKILL.md", ctx=ctx)

    assert 'api_key: "secret-xyz"' in restored
    assert "[Privacy Config Notice]" in restored
    assert "Configured but not referenced in content: region=cn" in restored


@pytest.mark.asyncio
async def test_privacy_config_service_uses_user_root(service):
    ctx = RequestContext(
        user=UserIdentifier("acme", "privacy_user_policy"),
        role=Role.USER,
    )
    await service.initialize_user_directories(ctx)

    privacy = service.privacy_configs
    await privacy.upsert(
        ctx=ctx,
        category="skill",
        target_key="policy-skill",
        values={"api_key": "secret-1"},
        updated_by=ctx.user.user_id,
    )

    current = await privacy.get_current(ctx, "skill", "policy-skill")
    assert current is not None


@pytest.mark.asyncio
async def test_placeholderization_only_replaces_structured_values(monkeypatch):
    content = """region: cn\nnotes: region is cn in docs\ndefault_env=prod\ntext: prod should stay here\n"""
    result = placeholderize_skill_content_with_blocks(
        content,
        "structured-skill",
        {"region": "cn", "env": "prod"},
    )

    assert result.replaced_values == {"region": "cn", "env": "prod"}
    assert result.sanitized_content == (
        "region: {{ov_privacy:skill:structured-skill:region}}\n"
        "notes: region is cn in docs\n"
        "default_env={{ov_privacy:skill:structured-skill:env}}\n"
        "text: prod should stay here\n"
    )

    async def fake_completion_async(_prompt):
        return '{"values": {"api_key": "different-secret"}}'

    monkeypatch.setattr(
        "openviking.privacy.skill_extractor.get_openviking_config",
        lambda: type(
            "Cfg",
            (),
            {
                "vlm": type(
                    "VLM", (), {"get_completion_async": staticmethod(fake_completion_async)}
                )()
            },
        )(),
    )

    content = 'api_key: "secret-xyz"\n'
    result = await extract_skill_privacy_values(
        skill_name="unmatched-skill",
        skill_description="skill with secret",
        content=content,
    )

    assert result.values == {}
    assert result.sanitized_content == content
    assert result.original_content_blocks == []
    assert result.replacement_content_blocks == []


def test_placeholderization_replaces_end_of_file_values_without_newline():
    result = placeholderize_skill_content_with_blocks(
        "api_key=secret-xyz",
        "eof-skill",
        {"api_key": "secret-xyz"},
    )

    assert result.replaced_values == {"api_key": "secret-xyz"}
    assert result.sanitized_content == "api_key={{ov_privacy:skill:eof-skill:api_key}}"


def test_placeholderization_replaces_all_structured_occurrences_for_same_value():
    result = placeholderize_skill_content_with_blocks(
        'api_key: "secret"\nbackup=secret\n',
        "multi-hit-skill",
        {"api_key": "secret"},
    )

    assert result.replaced_values == {"api_key": "secret"}
    assert result.sanitized_content == (
        'api_key: "{{ov_privacy:skill:multi-hit-skill:api_key}}"\n'
        "backup={{ov_privacy:skill:multi-hit-skill:api_key}}\n"
    )


def test_placeholderization_preserves_crlf_spacing_and_regex_special_values():
    result = placeholderize_skill_content_with_blocks(
        "token:\ta.b+c?[x]  \r\ntext: a.b+c?[x] should stay here\r\n",
        "regex-skill",
        {"token": "a.b+c?[x]"},
    )

    assert result.replaced_values == {"token": "a.b+c?[x]"}
    assert result.sanitized_content == (
        "token:\t{{ov_privacy:skill:regex-skill:token}}  \r\ntext: a.b+c?[x] should stay here\r\n"
    )


def test_placeholderization_preserves_quoted_value_matching():
    result = placeholderize_skill_content_with_blocks(
        'note: use "secret" in prose\n',
        "quoted-skill",
        {"api_key": "secret"},
    )

    assert result.replaced_values == {"api_key": "secret"}
    assert result.sanitized_content == (
        'note: use "{{ov_privacy:skill:quoted-skill:api_key}}" in prose\n'
    )
