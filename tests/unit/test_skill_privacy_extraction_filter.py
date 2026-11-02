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
async def test_content_write_skips_sanitizing_abstract_sidecars(monkeypatch):
    """System-generated .abstract.md sidecars must skip privacy substitution (#6909).

    The skip decision happens in ContentWriteCoordinator._write before any
    namespace-specific branching, so stub the namespace policy + policy guard and
    drive the INDEXED path far enough to observe whether the sanitizer ran.
    """
    from types import SimpleNamespace as NS

    from openviking.server.identity import RequestContext, Role
    from openviking.storage.content_write import ContentWriteCoordinator
    from openviking_cli.session.user_id import UserIdentifier

    sanitizer = NS()
    sanitizer.calls = []

    async def fake_sanitize(self, content, *, uri, ctx):
        sanitizer.calls.append(uri)
        return content

    monkeypatch.setattr(ContentWriteCoordinator, "_sanitize_text", fake_sanitize)

    fs = NS()
    coordinator = ContentWriteCoordinator(viking_fs=fs, privacy_sanitizer=sanitizer)

    async def noop_access(uri, ctx, action):
        return None

    fs._ensure_access = noop_access

    async def fail_deep(*args, **kwargs):
        raise AssertionError("deep write path should not run in this test")

    coordinator._write_memory_with_refresh = fail_deep
    coordinator._resolve_root_uri = fail_deep

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)
    sidecar_uri = "viking://agent/skills/deploy-helper/.abstract.md"
    body = "directory: viking://agent/skills/deploy-helper/"

    # The sanitize site sits before namespace handling; if nothing raises and the
    # sanitizer was never called, the sidecar skip held.
    try:
        await coordinator._write(uri=sidecar_uri, content=body, mode="replace", ctx=ctx)
    except Exception:
        pass

    assert sanitizer.calls == [], "sidecar content must not reach the privacy sanitizer"


@pytest.mark.asyncio
async def test_content_write_sanitizes_user_content_normally(monkeypatch):
    """User-authored SKILL.md content still goes through privacy substitution."""
    from types import SimpleNamespace as NS

    from openviking.server.identity import RequestContext, Role
    from openviking.storage.content_write import ContentWriteCoordinator
    from openviking_cli.session.user_id import UserIdentifier

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
        raise AssertionError("deep write path should not run in this test")

    coordinator._write_memory_with_refresh = fail_deep
    coordinator._resolve_root_uri = fail_deep

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)
    user_uri = "viking://agent/skills/deploy-helper/SKILL.md"

    try:
        await coordinator._write(
            uri=user_uri, content='api_key="sk-live-abc"', mode="replace", ctx=ctx
        )
    except Exception:
        pass

    assert sanitizer.calls == [user_uri], "user content must reach the privacy sanitizer"
