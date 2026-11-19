# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Remote Skill activation integrity tests."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from vikingbot.agent.remote_skill_cache import RemoteSkillSnapshotCache
from vikingbot.agent.remote_skills import (
    SkillRuntimeContext,
    SkillRuntimeError,
    _manifest_signature,
)
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import VikingMultiReadTool
from vikingbot.config.schema import Config, RemoteSkillsConfig, SessionKey


def _skill_md(name: str, body: str = "Use the Skill.") -> str:
    return f"""---
name: {name}
description: Remote Skill integrity fixture
---
{body}
"""


def _skill_result(
    root_uri: str,
    returned_content: str,
    *,
    stored_content: str | None = None,
    manifest_sha256: str | None = None,
    content_sha256: str | None = None,
    visible_content: str | None = None,
    stored_content_sha256: str | None = None,
) -> dict[str, object]:
    stored = returned_content if stored_content is None else stored_content
    stored_bytes = stored.encode("utf-8")
    visible = stored if visible_content is None else visible_content
    return {
        "root_uri": root_uri,
        "content": returned_content,
        "content_sha256": content_sha256
        or hashlib.sha256(returned_content.encode("utf-8")).hexdigest(),
        "visible_content_sha256": hashlib.sha256(visible.encode("utf-8")).hexdigest(),
        "stored_content_sha256": stored_content_sha256 or hashlib.sha256(stored_bytes).hexdigest(),
        "revision": "fixture-revision",
        "files": [
            {
                "path": "SKILL.md",
                "uri": f"{root_uri}/SKILL.md",
                "is_dir": False,
                "kind": "definition",
                "size": len(stored_bytes),
                "sha256": manifest_sha256 or hashlib.sha256(stored_bytes).hexdigest(),
            }
        ],
    }


class FakeClient:
    def __init__(
        self,
        result: dict[str, object],
        *,
        downloaded: bytes | None = None,
        download_error: Exception | None = None,
    ):
        self.result = result
        self.downloaded_bytes = downloaded
        self.download_error = download_error
        self.downloaded: list[str] = []
        self.get_skill_calls = 0

    async def get_skill(self, *args, **kwargs):
        self.get_skill_calls += 1
        return self.result

    async def download_bytes(self, uri: str) -> bytes:
        self.downloaded.append(uri)
        if self.download_error is not None:
            raise self.download_error
        assert self.downloaded_bytes is not None
        return self.downloaded_bytes


def _runtime(tmp_path, monkeypatch, client, **settings):
    runtime = SkillRuntimeContext(
        config=Config(
            storage_workspace=str(tmp_path),
            hooks=[],
            remote_skills=RemoteSkillsConfig(**settings),
        ),
        session_key=SessionKey(type="cli", channel_id="default", chat_id="remote-skills"),
        sandbox_manager=None,
        workspace_id="workspace",
        openviking_connection=None,
        actor_peer_id=None,
    )

    async def get_client():
        return client

    monkeypatch.setattr(runtime, "_get_client", get_client)
    return runtime


@pytest.mark.asyncio
async def test_unchanged_content_uses_manifest_without_download(tmp_path, monkeypatch):
    root_uri = "viking://user/demo/skills/plain-skill"
    skill_md_uri = f"{root_uri}/SKILL.md"
    content = _skill_md("plain-skill")
    client = FakeClient(_skill_result(root_uri, content))
    runtime = _runtime(tmp_path, monkeypatch, client)

    active = await runtime.activate_from_read(skill_md_uri, content)

    assert active is not None
    assert active.content == content
    assert client.get_skill_calls == 1
    assert client.downloaded == []


@pytest.mark.asyncio
async def test_unchanged_content_must_match_manifest(tmp_path, monkeypatch):
    root_uri = "viking://user/demo/skills/manifest-mismatch"
    skill_md_uri = f"{root_uri}/SKILL.md"
    content = _skill_md("manifest-mismatch")
    client = FakeClient(_skill_result(root_uri, content, manifest_sha256="0" * 64))
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(skill_md_uri, content)

    assert caught.value.code == "SKILL_REVISION_CHANGED"
    assert client.downloaded == []


@pytest.mark.asyncio
async def test_privacy_restoration_binds_stored_bytes_to_snapshot_and_manifest(
    tmp_path, monkeypatch
):
    placeholder = "{{ov_privacy:skill:privacy-demo:api_key}}"
    stored_content = _skill_md("privacy-demo", f"Use {placeholder}.")
    restored_content = stored_content.replace(placeholder, "test-value")
    root_uri = "viking://user/demo/skills/privacy-demo"
    skill_md_uri = f"{root_uri}/SKILL.md"
    client = FakeClient(
        _skill_result(root_uri, restored_content, stored_content=stored_content),
        downloaded=stored_content.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    active = await runtime.activate_from_read(skill_md_uri, stored_content)

    assert active is not None
    assert active.content == stored_content
    assert "test-value" not in active.content
    assert (
        active.files["SKILL.md"].sha256
        == hashlib.sha256(stored_content.encode("utf-8")).hexdigest()
    )
    assert client.downloaded == [skill_md_uri]


@pytest.mark.asyncio
async def test_activation_response_keeps_privacy_reference_masked(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:privacy-response:api_key}}"
    stored_content = _skill_md("privacy-response", 'Use "resolved-secret".')
    read_content = _skill_md("privacy-response", f"Use {placeholder}.")
    restored_content = (
        stored_content
        + "\n\n[Privacy Config Notice]\napi_key=resolved-secret\nunused=also-secret\n"
    )
    root_uri = "viking://user/demo/skills/privacy-response"
    skill_md_uri = f"{root_uri}/SKILL.md"
    activation_client = FakeClient(
        _skill_result(
            root_uri,
            restored_content,
            stored_content=stored_content,
            visible_content=read_content,
        ),
        downloaded=stored_content.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, activation_client)
    read_client = SimpleNamespace(read_content=AsyncMock(return_value=read_content))
    tool = VikingMultiReadTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=read_client))
    monkeypatch.setattr(tool, "_release_client", AsyncMock())

    result = await tool.execute(ToolContext(skill_runtime=runtime), uris=[skill_md_uri])

    assert isinstance(result, str)
    assert placeholder in result
    assert "resolved-secret" not in result
    assert "also-secret" not in result
    assert "[Privacy Config Notice]" not in result
    assert root_uri in runtime.active_skills
    read_client.read_content.assert_awaited_once_with(
        skill_md_uri, level="read", offset=0, limit=-1
    )


class FakeSandbox:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def remove_tree(self, path: str) -> None:
        prefix = f"{path.rstrip('/')}/"
        for filename in list(self.files):
            if filename == path or filename.startswith(prefix):
                self.files.pop(filename, None)


@pytest.mark.asyncio
async def test_privacy_restoration_materializes_verified_masked_read(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:privacy-materialized:api_key}}"
    stored_content = _skill_md("privacy-materialized", 'Use "resolved-secret".')
    read_content = _skill_md("privacy-materialized", f"Use {placeholder}.")
    restored_content = stored_content + "\n\n[Privacy Config Notice]\napi_key=resolved-secret\n"
    root_uri = "viking://user/demo/skills/privacy-materialized"
    client = FakeClient(
        _skill_result(
            root_uri,
            restored_content,
            stored_content=stored_content,
            visible_content=read_content,
        ),
        downloaded=stored_content.encode("utf-8"),
    )
    config = Config(
        storage_workspace=str(tmp_path),
        hooks=[],
        remote_skills=RemoteSkillsConfig(),
    )
    sandbox = FakeSandbox()
    sandbox_manager = SimpleNamespace(
        remote_skill_cache=RemoteSkillSnapshotCache(config),
        get_sandbox=AsyncMock(return_value=sandbox),
    )
    runtime = SkillRuntimeContext(
        config=config,
        session_key=SessionKey(type="cli", channel_id="default", chat_id="remote-skills"),
        sandbox_manager=sandbox_manager,
        workspace_id="workspace",
        openviking_connection=None,
        actor_peer_id=None,
    )

    async def get_client():
        return client

    monkeypatch.setattr(runtime, "_get_client", get_client)
    active = await runtime.activate_from_read(f"{root_uri}/SKILL.md", read_content)
    assert active is not None

    materialized_root = await runtime.materialize(active)

    assert sandbox.files[f"{materialized_root}/SKILL.md"] == read_content.encode("utf-8")
    cached_definitions = list((tmp_path / "bot" / "remote_skill_cache").rglob("SKILL.md"))
    assert len(cached_definitions) == 1
    cached_content = cached_definitions[0].read_text(encoding="utf-8")
    assert placeholder in cached_content
    assert "resolved-secret" not in cached_content


@pytest.mark.asyncio
async def test_restoration_download_failure_preserves_cause(tmp_path, monkeypatch):
    stored_content = _skill_md("download-failure", "Use stored value.")
    restored_content = _skill_md("download-failure", "Use restored value.")
    root_uri = "viking://user/demo/skills/download-failure"
    client = FakeClient(
        _skill_result(root_uri, restored_content, stored_content=stored_content),
        download_error=RuntimeError("HTTP 404 Not Found"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert caught.value.code == "SKILL_INTEGRITY_UNAVAILABLE"
    assert "HTTP 404 Not Found" in str(caught.value)


@pytest.mark.asyncio
async def test_restoration_rejects_downloaded_byte_mismatch(tmp_path, monkeypatch):
    stored_content = _skill_md("byte-mismatch", "Use stored value.")
    restored_content = _skill_md("byte-mismatch", "Use restored value.")
    root_uri = "viking://user/demo/skills/byte-mismatch"
    client = FakeClient(
        _skill_result(root_uri, restored_content, stored_content=stored_content),
        downloaded=b"different bytes",
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert caught.value.code == "SKILL_REVISION_CHANGED"


@pytest.mark.asyncio
async def test_restoration_enforces_stored_definition_limit(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:oversized-restoration:api_key}}"
    stored_content = _skill_md("oversized-restoration", f"Use {placeholder}. " + "x" * 256)
    restored_content = stored_content.replace(placeholder, "resolved-secret")
    root_uri = "viking://user/demo/skills/oversized-restoration"
    client = FakeClient(
        _skill_result(root_uri, restored_content, stored_content=stored_content),
        downloaded=stored_content.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client, max_file_bytes=128)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert caught.value.code == "SKILL_PACKAGE_TOO_LARGE"
    assert "Remote Skill file too large: SKILL.md" in str(caught.value)
    assert client.downloaded == [f"{root_uri}/SKILL.md"]


@pytest.mark.asyncio
async def test_restoration_accepts_sanitized_read_trigger(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:restoration-binding:api_key}}"
    stored_content = _skill_md("restoration-binding", 'Run with "resolved-secret".')
    restored_content = stored_content
    sanitized_trigger = _skill_md("restoration-binding", f"Run with {placeholder}.")
    root_uri = "viking://user/demo/skills/restoration-binding"
    client = FakeClient(
        _skill_result(
            root_uri,
            restored_content,
            stored_content=stored_content,
            visible_content=sanitized_trigger,
        ),
        downloaded=stored_content.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    active = await runtime.activate_from_read(f"{root_uri}/SKILL.md", sanitized_trigger)

    assert active is not None
    assert active.content == sanitized_trigger
    assert "resolved-secret" not in active.content
    assert placeholder in active.content


@pytest.mark.asyncio
async def test_restoration_requires_visible_read_checksum(tmp_path, monkeypatch):
    stored_content = _skill_md("missing-visible-checksum", "Run the reviewed workflow.")
    restored_content = _skill_md("missing-visible-checksum", "Run the restored workflow.")
    root_uri = "viking://user/demo/skills/missing-visible-checksum"
    result = _skill_result(root_uri, restored_content, stored_content=stored_content)
    result.pop("visible_content_sha256")
    client = FakeClient(result, downloaded=stored_content.encode("utf-8"))
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert caught.value.code == "SKILL_INTEGRITY_UNAVAILABLE"
    assert client.downloaded == []


@pytest.mark.asyncio
async def test_restoration_rejects_read_from_stale_snapshot(tmp_path, monkeypatch):
    original = _skill_md("stale-read", "Run version one.")
    current = _skill_md("stale-read", "Run version two.")
    root_uri = "viking://user/demo/skills/stale-read"
    client = FakeClient(
        _skill_result(root_uri, current, stored_content=current),
        downloaded=current.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", original)

    assert caught.value.code == "SKILL_REVISION_CHANGED"
    assert "Initial SKILL.md read" in str(caught.value)
    assert client.downloaded == []


@pytest.mark.asyncio
async def test_restoration_requires_snapshot_checksum(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:missing-checksum:api_key}}"
    stored_content = _skill_md("missing-checksum", f"Use {placeholder}.")
    restored_content = stored_content.replace(placeholder, "resolved-secret")
    root_uri = "viking://user/demo/skills/missing-checksum"
    result = _skill_result(root_uri, restored_content, stored_content=stored_content)
    result.pop("stored_content_sha256")
    client = FakeClient(result, downloaded=stored_content.encode("utf-8"))
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert caught.value.code == "SKILL_INTEGRITY_UNAVAILABLE"
    assert client.downloaded == []


@pytest.mark.asyncio
async def test_restoration_rejects_mismatched_snapshot_checksum(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:checksum-binding:api_key}}"
    stored_content = _skill_md("checksum-binding", f"Use {placeholder}.")
    restored_content = stored_content.replace(placeholder, "resolved-secret")
    root_uri = "viking://user/demo/skills/checksum-binding"
    client = FakeClient(
        _skill_result(
            root_uri,
            restored_content,
            stored_content=stored_content,
            stored_content_sha256="0" * 64,
        ),
        downloaded=stored_content.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert caught.value.code == "SKILL_REVISION_CHANGED"
    assert client.downloaded == [f"{root_uri}/SKILL.md"]


@pytest.mark.asyncio
async def test_restoration_accepts_quoted_frontmatter_and_notice(tmp_path, monkeypatch):
    placeholder = "{{ov_privacy:skill:quoted-restoration:api_key}}"
    stored_content = f"""---
name: quoted-restoration
description: 'issued to {placeholder}'
---
Use {placeholder}.
"""
    restored_content = stored_content.replace(
        f"description: 'issued to {placeholder}'",
        'description: "issued to resolved-secret"',
    ).replace(placeholder, "resolved-secret")
    restored_content += "\n\n[Privacy Config Notice]\nunused=also-secret\n"
    root_uri = "viking://user/demo/skills/quoted-restoration"
    client = FakeClient(
        _skill_result(root_uri, restored_content, stored_content=stored_content),
        downloaded=stored_content.encode("utf-8"),
    )
    runtime = _runtime(tmp_path, monkeypatch, client)

    active = await runtime.activate_from_read(f"{root_uri}/SKILL.md", stored_content)

    assert active is not None
    assert active.content == stored_content
    assert "resolved-secret" not in active.content
    assert "also-secret" not in active.content


@pytest.mark.asyncio
async def test_empty_activation_read_is_rejected(tmp_path, monkeypatch):
    root_uri = "viking://user/demo/skills/empty-trigger"
    client = FakeClient(_skill_result(root_uri, _skill_md("empty-trigger")))
    runtime = _runtime(tmp_path, monkeypatch, client)

    with pytest.raises(SkillRuntimeError) as caught:
        await runtime.activate_from_read(f"{root_uri}/SKILL.md", "")

    assert caught.value.code == "SKILL_INVALID"
    assert client.get_skill_calls == 0


def test_activation_call_requires_complete_read(tmp_path, monkeypatch):
    client = FakeClient({})
    runtime = _runtime(tmp_path, monkeypatch, client)
    uri = "viking://user/demo/skills/full-read/SKILL.md"

    assert runtime.is_activation_call(
        "openviking_multi_read", {"uris": [uri], "offset": 0, "limit": -1}
    )
    assert runtime.is_activation_call("openviking_multi_read", {"uris": [uri]})
    assert runtime.is_activation_call(
        "openviking_multi_read", {"uris": [uri], "offset": "0", "limit": "-1"}
    )
    assert not runtime.is_activation_call(
        "openviking_multi_read", {"uris": [uri], "offset": 0, "limit": 1}
    )
    assert not runtime.is_activation_call(
        "openviking_multi_read", {"uris": [uri], "offset": 1, "limit": -1}
    )
    assert not runtime.is_activation_call(
        "openviking_multi_read", {"uris": [uri], "with_revision": True}
    )
    assert not runtime.is_activation_call("read_file", {"uris": [uri]})


@pytest.mark.asyncio
async def test_windowed_skill_read_neither_activates_nor_expands(tmp_path, monkeypatch):
    client = FakeClient({})
    runtime = _runtime(tmp_path, monkeypatch, client)
    runtime.activate_from_read = AsyncMock()
    runtime.note_remote_read = Mock()
    tool = VikingMultiReadTool()
    read_client = SimpleNamespace(read_content=AsyncMock(return_value="line 2\n"))
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=read_client))
    monkeypatch.setattr(tool, "_release_client", AsyncMock())
    uri = "viking://user/demo/skills/windowed/SKILL.md"

    result = await tool.execute(ToolContext(skill_runtime=runtime), uris=[uri], offset=1, limit=1)

    assert isinstance(result, str)
    assert "line 2\n" in result
    assert "line 1" not in result
    runtime.activate_from_read.assert_not_awaited()
    runtime.note_remote_read.assert_called_once_with(uri)
    read_client.read_content.assert_awaited_once_with(uri, level="read", offset=1, limit=1)


@pytest.mark.asyncio
async def test_string_full_read_arguments_are_normalized_and_activate(tmp_path, monkeypatch):
    client = FakeClient({})
    runtime = _runtime(tmp_path, monkeypatch, client)
    runtime.activate_from_read = AsyncMock(return_value=None)
    tool = VikingMultiReadTool()
    read_client = SimpleNamespace(read_content=AsyncMock(return_value="complete definition\n"))
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=read_client))
    monkeypatch.setattr(tool, "_release_client", AsyncMock())
    uri = "viking://user/demo/skills/string-full-read/SKILL.md"

    result = await tool.execute(
        ToolContext(skill_runtime=runtime), uris=[uri], offset="0", limit="-1"
    )

    assert isinstance(result, str)
    assert "Error" not in result
    runtime.activate_from_read.assert_awaited_once_with(uri, "complete definition\n")
    read_client.read_content.assert_awaited_once_with(uri, level="read", offset=0, limit=-1)


def test_manifest_signature_ignores_restored_content_checksum():
    root_uri = "viking://user/demo/skills/signature"
    first = _skill_result(root_uri, _skill_md("signature"))
    second = {**first, "content_sha256": "f" * 64}

    assert _manifest_signature(first) == _manifest_signature(second)
