# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Skill privacy extraction must not register bare resource URIs (#6909).

An assignment-style URI in a SKILL.md (e.g. ``target_uri="viking://agent/skills"``)
is a location reference, not a secret. Registering it as a private value rewrites
every occurrence in stored content — including system-generated ``.abstract.md``
frontmatter, where the substituted ``{{ov_privacy:...}}`` scalar breaks the YAML
parse and silently strips the skill's description.
"""

import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.privacy.skill_extractor import _is_resource_location, extract_skill_privacy_values


def _vlm_returning(payload: dict) -> SimpleNamespace:
    async def get_completion_async(_prompt: str) -> str:
        return json.dumps(payload)

    return SimpleNamespace(get_completion_async=get_completion_async)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("viking://agent/skills", True),
        ("viking://user/alice/memories/preferences/Jane Doe/topic.md", True),
        ("https://internal.example.com/health", True),
        ("C:\\Users\\operator\\credentials.txt", True),
        ("/etc/openviking/ov.conf", True),
        # Review follow-up (#6909): locations that were previously missed.
        ("\\\\fileserver.internal\\share\\skills", True),
        ("./secrets/api.key", True),
        ("../configs/ov.conf", True),
        ('"viking://agent/skills"', True),
        ("'viking://agent/skills'", True),
        ("  /etc/openviking/ov.conf  ", True),
        # Credential-shaped URIs are secrets that happen to be URIs — keep them.
        ("https://user:S3cretP%40ss@internal.example.com/api", False),
        ("postgresql://ov:p%40ss@db.internal:5432/context", False),
        ("https://api.example.com/v1/x?token=ghp_abcdef123456", False),
        ("https://api.example.com/v1/x?page=1&api_key=sk-live-abc", False),
        ("https://api.example.com/v1/x?signature=deadbeef", False),
        # Plain secrets stay registered.
        ("sk-live-abcdef123456", False),
        ("aGVsbG8gd29ybGQ=", False),
        ("my secret pass phrase", False),
        ("", False),
    ],
)
def test_is_resource_location(value: str, expected: bool):
    assert _is_resource_location(value) is expected


@pytest.mark.asyncio
async def test_extraction_drops_bare_uri_values():
    vlm = _vlm_returning(
        {
            "values": {
                "target_uri": "viking://agent/skills",
                "api_key": "sk-live-abcdef123456",
            }
        }
    )

    result = await extract_skill_privacy_values(
        skill_name="deploy-helper",
        skill_description="Installs skills",
        content='Install with target_uri="viking://agent/skills" and api_key="sk-live-abcdef123456"',
        vlm=vlm,
    )

    assert "target_uri" not in result.values
    assert result.values == {"api_key": "sk-live-abcdef123456"}


@pytest.mark.asyncio
async def test_extraction_keeps_secret_shaped_values():
    vlm = _vlm_returning({"values": {"api_key": "sk-live-abcdef123456"}})

    result = await extract_skill_privacy_values(
        skill_name="deploy-helper",
        skill_description="Installs skills",
        content='api_key="sk-live-abcdef123456"',
        vlm=vlm,
    )

    assert result.values == {"api_key": "sk-live-abcdef123456"}


@pytest.mark.asyncio
async def test_extraction_with_all_locations_returns_no_values():
    vlm = _vlm_returning(
        {"values": {"target_uri": "viking://agent/skills", "docs_path": "/docs/guide.md"}}
    )

    result = await extract_skill_privacy_values(
        skill_name="deploy-helper",
        skill_description="Installs skills",
        content='target_uri="viking://agent/skills"',
        vlm=vlm,
    )

    assert result.values == {}
    assert result.sanitized_content == 'target_uri="viking://agent/skills"'


@pytest.mark.asyncio
async def test_native_sidecar_writes_preserve_metadata_and_redact_body(tmp_path):
    from openviking.core.context import ContextLevel
    from openviking.privacy.reference import build_privacy_field_reference
    from openviking.privacy.sanitizer import PrivacySanitizer
    from openviking.privacy.service import UserPrivacyConfigService
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.abstract_overview import (
        parse_abstract_overview,
        render_abstract_overview,
        write_abstract_overview,
    )
    from openviking.storage.content_write import ContentWriteCoordinator
    from openviking.storage.viking_fs import VikingFS
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
    from openviking_cli.session.user_id import UserIdentifier
    from openviking_cli.utils.config.agfs_config import AGFSConfig

    native = create_agfs_client(RagfsBindingConfig(AGFSConfig(path=str(tmp_path))))
    try:
        fs = VikingFS(native)
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        privacy = UserPrivacyConfigService(fs)
        sanitizer = PrivacySanitizer(privacy)
        fs.set_privacy_sanitizer(sanitizer)
        coordinator = ContentWriteCoordinator(fs, privacy_sanitizer=sanitizer)
        dir_uri = "viking://resources/privacy-native-validation"
        secret = secrets.token_urlsafe(24)
        await privacy.upsert(
            ctx,
            "credential",
            "native-validation",
            {"key": secret, "old_location": dir_uri},
            updated_by=ctx.user.user_id,
        )
        reference = build_privacy_field_reference(
            ctx.user.user_id, "credential", "native-validation", "key"
        )
        body = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")
        body += f"\n{secret}\n{dir_uri}\n"
        metadata = {
            "source": {"kind": "viking", "uri": dir_uri + "/README.md"},
            "generated_by": {"component": "openviking", "trigger": "native-validation"},
        }
        for iteration in range(2):
            await write_abstract_overview(
                viking_fs=fs,
                dir_uri=dir_uri,
                overview=body,
                abstract=body,
                ctx=ctx,
                is_stale=lambda: False,
                metadata=metadata,
            )
            for name, level in (
                (".abstract.md", ContextLevel.ABSTRACT),
                (".overview.md", ContextLevel.OVERVIEW),
            ):
                uri = f"{dir_uri}/{name}"
                raw = await fs.read_file(uri, ctx=ctx)
                document = parse_abstract_overview(raw)
                assert document.metadata == {"directory": dir_uri + "/", **metadata}
                assert secret not in document.body
                assert reference in document.body
                assert dir_uri not in document.body
                original = render_abstract_overview(level, dir_uri, body, metadata)
                assert await coordinator._sanitize_sidecar_text(original, uri=uri, ctx=ctx) == raw
                operations = [{"uri": uri, "content": original}]
                await coordinator._sanitize_batch_operations(operations, ctx=ctx)
                assert operations[0]["content"] == raw
            body += f"\nRevision {iteration + 1}: {secret}\n"
        for name in ("SKILL.md", ".abstract.md", ".overview.md"):
            uri = f"{dir_uri}/legacy/{name}"
            await fs.write_file(uri, secret, ctx=ctx)
            assert await fs.read_file(uri, ctx=ctx) == reference
    finally:
        native.close()
