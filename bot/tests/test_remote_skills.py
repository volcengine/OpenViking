# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Remote Skill activation and Privacy regressions."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.remote_skill_cache import RemoteSkillSnapshotCache
from vikingbot.agent.remote_skills import SkillRuntimeContext, SkillRuntimeError
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import VikingMultiReadTool
from vikingbot.config.schema import Config, SessionKey

_ROOT = "viking://user/demo/skills/privacy-demo"
_URI = f"{_ROOT}/SKILL.md"
_SKILL_REF = "{{ov_privacy:skill:privacy-demo:api_key}}"
_CREDENTIAL_REF = "{{ov_privacy:demo:viking://~/privacy/credential/example#/values/token}}"


def _skill_md(body, *, description="Remote Skill fixture"):
    return f"---\nname: privacy-demo\ndescription: {description}\n---\n{body}\n"


def _client(stored, *, visible=None, detail=None):
    data = stored.encode()
    visible = stored if visible is None else visible
    detail = stored if detail is None else detail
    checksum = hashlib.sha256(data).hexdigest()
    return SimpleNamespace(
        stat=AsyncMock(return_value={"size": len(data)}),
        read_content=AsyncMock(return_value=visible),
        download_bytes=AsyncMock(return_value=data),
        get_skill=AsyncMock(
            return_value={
                "root_uri": _ROOT,
                "revision": "fixture-revision",
                "content": detail,
                "content_sha256": hashlib.sha256(detail.encode()).hexdigest(),
                "visible_content_sha256": hashlib.sha256(visible.encode()).hexdigest(),
                "stored_content_sha256": checksum,
                "files": [
                    {
                        "path": "SKILL.md",
                        "uri": _URI,
                        "size": len(data),
                        "sha256": checksum,
                    }
                ],
            }
        ),
    )


def _runtime(tmp_path, client, *, sandbox=None):
    config = Config(storage_workspace=str(tmp_path), hooks=[])
    manager = None
    if sandbox is not None:
        manager = SimpleNamespace(
            remote_skill_cache=RemoteSkillSnapshotCache(config),
            get_sandbox=AsyncMock(return_value=sandbox),
        )
    runtime = SkillRuntimeContext(
        config=config,
        session_key=SessionKey(type="cli", channel_id="default", chat_id="remote-skills"),
        sandbox_manager=manager,
        workspace_id="workspace",
        openviking_connection=None,
        actor_peer_id=None,
    )
    runtime._client = client
    return runtime


@pytest.mark.asyncio
async def test_skill_read_and_materialization_keep_privacy_references(tmp_path):
    registered_value = "sample-registered-setting"
    resolved_value = "sample-restored-setting"
    unused_value = "sample-unused-setting"
    stored = _skill_md(
        f"Use {_SKILL_REF} and {registered_value}.",
        description=f"'Use {_SKILL_REF}'",
    )
    visible = stored.replace(registered_value, _CREDENTIAL_REF)
    detail = stored.replace(_SKILL_REF, resolved_value).replace(
        f"'Use {resolved_value}'", f'"Use {resolved_value}"'
    )
    detail += f"\n[Privacy Config Notice]\nunused={unused_value}\n"
    client = _client(stored, visible=visible, detail=detail)
    sandbox = SimpleNamespace(write_file_bytes=AsyncMock(), remove_tree=AsyncMock())
    runtime = _runtime(tmp_path, client, sandbox=sandbox)

    result = await VikingMultiReadTool(client=client).execute(
        ToolContext(skill_runtime=runtime), uris=[_URI]
    )

    assert visible in result
    assert all(value not in result for value in (registered_value, resolved_value, unused_value))
    assert "[Privacy Config Notice]" not in result
    active = runtime.active_skills[_ROOT]
    path = await runtime.materialize(active)
    sandbox.write_file_bytes.assert_awaited_once_with(f"{path}/SKILL.md", visible.encode())
    [cached] = list(runtime.config.bot_data_path.glob("remote_skill_cache/**/SKILL.md"))
    assert cached.read_bytes() == visible.encode()


@pytest.mark.asyncio
async def test_skill_read_rejects_stale_privacy_view(tmp_path):
    value = "sample-registered-setting"
    initial = _skill_md(f"Use {value}.")
    # The stored file and detail are unchanged, but a new Credential now masks the read.
    client = _client(initial, visible=initial.replace(value, _CREDENTIAL_REF))
    client.read_content.return_value = initial
    runtime = _runtime(tmp_path, client)

    result = await VikingMultiReadTool(client=client).execute(
        ToolContext(skill_runtime=runtime), uris=[_URI]
    )

    assert "SKILL_REVISION_CHANGED" in result
    assert value not in result
    assert not runtime.active_skills
    client.download_bytes.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_before_detail", [True, False], ids=["read", "download"])
async def test_skill_activation_rejects_another_revision(tmp_path, changed_before_detail):
    stored = _skill_md(f"Run version B with {_SKILL_REF}.")
    older = stored.replace("version B", "version A")
    client = _client(stored, detail=stored.replace(_SKILL_REF, "sample-restored-setting"))
    initial = older if changed_before_detail else stored
    if not changed_before_detail:
        client.download_bytes.return_value = older.encode()
    runtime = _runtime(tmp_path, client)

    with pytest.raises(SkillRuntimeError, match="SKILL_REVISION_CHANGED"):
        await runtime.activate_from_read(_URI, initial)

    assert not runtime.active_skills


@pytest.mark.asyncio
async def test_ordinary_skill_uses_manifest_on_an_older_server(tmp_path):
    stored = _skill_md("Use this Skill.")
    client = _client(stored)
    for field in ("visible_content_sha256", "stored_content_sha256"):
        del client.get_skill.return_value[field]
    runtime = _runtime(tmp_path, client)

    assert await runtime.activate_from_read(_URI, stored)
    client.download_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_privacy_view_change_rejects_stale_reuse_and_replaces_cache(tmp_path):
    value = "sample-registered-setting"
    stored = _skill_md(f"Use {value}.")
    masked = stored.replace(value, _CREDENTIAL_REF)
    sandbox = SimpleNamespace(write_file_bytes=AsyncMock(), remove_tree=AsyncMock())

    # 1) After activation, a changed same-turn read or materialization fails closed.
    client = _client(stored)
    runtime = _runtime(tmp_path, client, sandbox=sandbox)
    tool = VikingMultiReadTool(client=client)
    active = await runtime.activate_from_read(_URI, stored)

    client.read_content.return_value = masked
    reread = await tool.execute(ToolContext(skill_runtime=runtime), uris=[_URI])
    assert "SKILL_REVISION_CHANGED" in reread
    assert value not in reread

    client.get_skill.return_value = {
        **client.get_skill.return_value,
        "visible_content_sha256": hashlib.sha256(masked.encode()).hexdigest(),
    }
    with pytest.raises(SkillRuntimeError, match="SKILL_REVISION_CHANGED"):
        await runtime.materialize(active)
    sandbox.write_file_bytes.assert_not_awaited()

    # 2) Next turn with the masked view replaces the cached plaintext copy in place.
    c1 = _client(stored)
    rt1 = _runtime(tmp_path, c1, sandbox=sandbox)
    await rt1.materialize(await rt1.activate_from_read(_URI, stored))

    c2 = _client(stored, visible=masked)
    rt2 = _runtime(tmp_path, c2, sandbox=sandbox)
    rt2._snapshot_cache = rt1._snapshot_cache
    path = await rt2.materialize(await rt2.activate_from_read(_URI, masked))

    assert sandbox.write_file_bytes.await_args.args == (f"{path}/SKILL.md", masked.encode())
    cached_files = list(rt2.config.bot_data_path.glob("remote_skill_cache/**/SKILL.md"))
    assert len(cached_files) == 1
    assert cached_files[0].read_bytes() == masked.encode()


@pytest.mark.asyncio
async def test_skill_activation_rejects_oversized_visible_read(tmp_path):
    stored = _skill_md("secret123")
    masked = stored.replace("secret123", _CREDENTIAL_REF)
    client = _client(stored, visible=masked)
    runtime = _runtime(tmp_path, client)
    runtime.settings.max_file_bytes = len(stored.encode()) + 5

    with pytest.raises(SkillRuntimeError, match="SKILL_PACKAGE_TOO_LARGE"):
        await runtime.activate_from_read(_URI, masked)

    assert not runtime.active_skills
    client.get_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_windowed_skill_read_keeps_requested_lines(tmp_path):
    selected = "Use the second line.\n"
    client = _client(_skill_md(f"Use the first line.\n{selected}"))
    client.read_content.return_value = selected
    runtime = _runtime(tmp_path, client)
    tool = VikingMultiReadTool(client=client)

    assert not runtime.is_activation_call(tool.name, {"uris": [_URI], "offset": "0", "limit": "-1"})
    assert tool.validate_params({"uris": [_URI], "offset": "0", "limit": "-1"})
    assert not runtime.is_activation_call(tool.name, {"uris": [_URI], "with_revision": True})

    result = await tool.execute(ToolContext(skill_runtime=runtime), uris=[_URI], offset=5, limit=1)

    assert selected in result
    assert "Use the first line." not in result
    client.read_content.assert_awaited_once_with(
        _URI, level="read", offset=5, limit=1, raise_on_error=True
    )
    assert not runtime.active_skills
