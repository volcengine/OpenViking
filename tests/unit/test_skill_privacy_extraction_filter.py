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
from types import SimpleNamespace

import pytest
import yaml

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
async def test_content_write_sidecar_frontmatter_exempt_body_sanitized(monkeypatch):
    """System sidecar frontmatter skips privacy substitution; the body does not (#6911).

    ``ContentWriteCoordinator._write`` splits a ``.abstract.md`` / ``.overview.md``
    payload into frontmatter and body: the system-generated frontmatter keeps its
    canonical URIs verbatim (a substituted ``{{ov_privacy:...}}`` scalar breaks the
    YAML parse, #6909), while the generated body still goes through the sanitizer.
    """
    from types import SimpleNamespace as NS

    from openviking.server.identity import RequestContext, Role
    from openviking.storage.content_write import ContentWriteCoordinator
    from openviking_cli.session.user_id import UserIdentifier

    class _SanitizeCompleted(Exception):
        """Sentinel raised past the sanitize site before the deep write path."""

    sanitizer = NS()
    sanitizer.calls = []

    async def fake_sanitize(self, content, *, uri, ctx):
        sanitizer.calls.append((uri, content))
        return "SANITIZED-BODY"

    monkeypatch.setattr(ContentWriteCoordinator, "_sanitize_text", fake_sanitize)

    fs = NS()
    coordinator = ContentWriteCoordinator(viking_fs=fs, privacy_sanitizer=sanitizer)

    async def noop_access(uri, ctx, action):
        return None

    fs._ensure_access = noop_access

    deep_calls = []

    async def fail_deep(*args, **kwargs):
        deep_calls.append(kwargs.get("content") if "content" in kwargs else None)
        raise _SanitizeCompleted("deep write path should not run in this test")

    async def resolve_root(*args, **kwargs):
        return "viking://agent/skills/deploy-helper"

    # .abstract.md re-embeds in place via _write_abstract_overview_with_refresh.
    coordinator._write_abstract_overview_with_refresh = fail_deep
    coordinator._write_memory_with_refresh = fail_deep
    coordinator._write_direct_with_refresh = fail_deep
    coordinator._resolve_root_uri = resolve_root

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)
    sidecar_uri = "viking://agent/skills/deploy-helper/.abstract.md"
    frontmatter = (
        "---\ndirectory: viking://agent/skills/deploy-helper/\ngenerated_by: openviking\n---\n"
    )
    body = "\nThe helper reads {{ov_privacy:deploy-helper#values/api_key}} at runtime.\n"
    content = frontmatter + body

    # The only exception allowed to surface is the deep-path sentinel: anything
    # else (e.g. a sanitizer error) must fail the test instead of being swallowed.
    with pytest.raises(_SanitizeCompleted):
        await coordinator._write(uri=sidecar_uri, content=content, mode="replace", ctx=ctx)

    # The body — and only the body — reached the sanitizer.
    assert sanitizer.calls == [(sidecar_uri, body)], "sidecar body must be sanitized"

    # The written payload keeps the frontmatter verbatim and carries the
    # sanitized body; the frontmatter must still be real, parseable YAML.
    assert deep_calls == [frontmatter + "SANITIZED-BODY"]
    written = deep_calls[0]
    assert written.startswith("---\n")
    closing = written.index("\n---\n", 4)
    metadata = yaml.safe_load(written[4:closing])
    assert metadata["directory"] == "viking://agent/skills/deploy-helper/"
    assert "SANITIZED-BODY" in written[closing + len("\n---\n") :]
    assert "{{ov_privacy:" not in written[closing + len("\n---\n") :]


@pytest.mark.asyncio
async def test_content_write_sanitizes_user_content_normally(monkeypatch):
    """User-authored SKILL.md content still goes through privacy substitution."""
    from types import SimpleNamespace as NS

    from openviking.server.identity import RequestContext, Role
    from openviking.storage.content_write import ContentWriteCoordinator
    from openviking_cli.session.user_id import UserIdentifier

    class _SanitizeCompleted(Exception):
        """Sentinel raised past the sanitize site before the deep write path."""

    sanitizer = NS()
    sanitizer.calls = []

    async def fake_sanitize(self, content, *, uri, ctx):
        sanitizer.calls.append(uri)
        return "SANITIZED"

    monkeypatch.setattr(ContentWriteCoordinator, "_sanitize_text", fake_sanitize)

    fs = NS()
    coordinator = ContentWriteCoordinator(viking_fs=fs, privacy_sanitizer=sanitizer)

    async def noop_access(uri, ctx, action):
        return None

    fs._ensure_access = noop_access

    async def fail_deep(*args, **kwargs):
        raise _SanitizeCompleted("deep write path should not run in this test")

    coordinator._write_memory_with_refresh = fail_deep
    coordinator._write_direct_with_refresh = fail_deep
    coordinator._resolve_root_uri = fail_deep

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)
    user_uri = "viking://agent/skills/deploy-helper/SKILL.md"

    with pytest.raises(_SanitizeCompleted):
        await coordinator._write(
            uri=user_uri, content='api_key="sk-live-abc"', mode="replace", ctx=ctx
        )

    assert sanitizer.calls == [user_uri], "user content must reach the privacy sanitizer"
