# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.privacy.skill_extractor import extract_skill_privacy_values
from openviking.privacy.skill_placeholder import placeholderize_skill_content_with_blocks
from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


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

    restored = await privacy.activate_version(ctx, "skill", "demo-skill", 1, updated_by=ctx.user.user_id)
    current = await privacy.get_current(ctx, "skill", "demo-skill")
    assert restored.version == 1
    assert current.version == 1
    assert current.values["api_key"] == "secret-1"


@pytest.mark.asyncio
async def test_skill_read_restores_placeholder(service):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_restore"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    await service.initialize_agent_directories(ctx)

    await service.resources.add_skill(
        data={
            "name": "restore-skill",
            "description": "skill with secret",
            "content": 'api_key: "secret-xyz"\nbase_url: "https://example.com"\n',
        },
        ctx=ctx,
        wait=False,
    )

    stored = await service.viking_fs.read_file("viking://agent/skills/restore-skill/SKILL.md", ctx=ctx)
    restored = await service.fs.read("viking://agent/skills/restore-skill/SKILL.md", ctx=ctx)

    assert "secret-xyz" not in stored
    assert "{{ov_privacy:skill:restore-skill:api_key}}" in stored
    assert "secret-xyz" in restored
    assert "https://example.com" in restored


@pytest.mark.asyncio
async def test_skill_read_restores_placeholder_with_agent_segment(service):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_restore_agent_segment"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    await service.initialize_agent_directories(ctx)

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
        "viking://agent/default/skills/restore-skill-agent-segment/SKILL.md",
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
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_partial_restore"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    await service.initialize_agent_directories(ctx)

    skill_uri = "viking://agent/skills/partial-restore-skill"
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
    assert '{{ov_privacy:skill:partial-restore-skill:region}}' in restored
    assert "[Privacy Config Notice]" in restored
    assert "Missing config: region=<missing>" in restored


@pytest.mark.asyncio
async def test_skill_read_appends_notice_for_extra_configured_keys(service):
    ctx = RequestContext(user=UserIdentifier.the_default_user("privacy_extra_config"), role=Role.ROOT)
    await service.initialize_user_directories(ctx)
    await service.initialize_agent_directories(ctx)

    skill_uri = "viking://agent/skills/extra-config-skill"
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
async def test_privacy_config_service_uses_policy_aware_user_root(service):
    ctx = RequestContext(
        user=UserIdentifier("acme", "privacy_user_policy", "demo-agent"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(isolate_user_scope_by_agent=True),
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
    stored = await service.viking_fs.read_file(
        "viking://user/privacy_user_policy/agent/demo-agent/privacy/skill/policy-skill/current.json",
        ctx=ctx,
    )

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
        lambda: type("Cfg", (), {"vlm": type("VLM", (), {"get_completion_async": staticmethod(fake_completion_async)})()})(),
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
        'backup={{ov_privacy:skill:multi-hit-skill:api_key}}\n'
    )
