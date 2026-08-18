from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.remote_skill_cache import (
    RemoteSkillCacheKey,
    RemoteSkillSnapshotCache,
    SnapshotBuildResult,
)
from vikingbot.agent.remote_skills import SkillRuntimeContext, SkillRuntimeError
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.filesystem import ReadFileTool
from vikingbot.agent.tools.ov_file import VikingAddResourceTool, VikingMultiReadTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.agent.tools.shell import ExecTool
from vikingbot.bus.events import OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config, SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.builtins.openviking_hooks import OpenVikingPostCallHook
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from vikingbot.sandbox.backends.aiosandbox import AioSandboxBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend
from vikingbot.sandbox.base import SandboxBackend, SandboxExecutionError
from vikingbot.sandbox.manager import SandboxManager

SKILL_URI = "viking://user/u1/skills/release-audit"
SKILL_MD_URI = f"{SKILL_URI}/SKILL.md"
SKILL_MD = """---
name: release-audit
description: Audit a release
allowed-tools: Read Bash(python *)
metadata:
  vikingbot:
    requires:
      bins: [python3]
---
Run `python scripts/audit.py`.
"""


class _FakeSandbox:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.removed: list[str] = []
        self.exported: list[str] = []
        self.requirements_ok = True

    def local_file_path(self, path: str) -> Path | None:
        del path
        return None

    async def export_file(
        self,
        path: str,
        destination: Path,
        *,
        max_bytes: int | None = None,
    ) -> int:
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]
        if max_bytes is not None and len(content) > max_bytes:
            raise ValueError("file exceeds export limit")
        destination.write_bytes(content)
        self.exported.append(path)
        return len(content)

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = bytes(content)

    async def read_file(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path].decode("utf-8")

    async def read_file_bytes(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def remove_tree(self, path: str) -> None:
        self.removed.append(path)
        prefix = path.rstrip("/") + "/"
        self.files = {key: value for key, value in self.files.items() if not key.startswith(prefix)}

    async def execute(self, command: str, timeout: int = 60) -> str:
        del command, timeout
        return "__VIKINGBOT_SKILL_REQUIREMENTS_OK__" if self.requirements_ok else "Exit code: 1"


class _FakeSandboxManager:
    def __init__(self):
        self.sandbox = _FakeSandbox()
        self.get_sandbox_calls = 0

    def to_workspace_id(self, session_key: SessionKey) -> str:
        return session_key.safe_name()

    async def get_sandbox(self, session_key: SessionKey) -> _FakeSandbox:
        del session_key
        self.get_sandbox_calls += 1
        return self.sandbox


class _FakeClient:
    def __init__(self):
        self.closed = False
        self.download_calls: list[str] = []
        self.get_skill_calls = 0
        self.downloads = {
            f"{SKILL_URI}/scripts/audit.py": b"print('ok')\n",
            f"{SKILL_URI}/references/checklist.md": b"# Checklist\n",
        }
        self.skill_result = {
            "name": "release-audit",
            "root_uri": SKILL_URI,
            "content": SKILL_MD,
            "content_sha256": hashlib.sha256(SKILL_MD.encode()).hexdigest(),
            "revision": "revision-1",
            "files": [
                {
                    "path": "SKILL.md",
                    "uri": SKILL_MD_URI,
                    "is_dir": False,
                    "kind": "definition",
                    "size": len(SKILL_MD.encode()),
                    "sha256": hashlib.sha256(SKILL_MD.encode()).hexdigest(),
                },
                {
                    "path": "scripts",
                    "uri": f"{SKILL_URI}/scripts",
                    "is_dir": True,
                    "kind": "directory",
                },
                {
                    "path": "scripts/audit.py",
                    "uri": f"{SKILL_URI}/scripts/audit.py",
                    "is_dir": False,
                    "kind": "auxiliary",
                    "size": len(self.downloads[f"{SKILL_URI}/scripts/audit.py"]),
                    "sha256": hashlib.sha256(
                        self.downloads[f"{SKILL_URI}/scripts/audit.py"]
                    ).hexdigest(),
                },
                {
                    "path": "references/checklist.md",
                    "uri": f"{SKILL_URI}/references/checklist.md",
                    "is_dir": False,
                    "kind": "auxiliary",
                    "size": len(self.downloads[f"{SKILL_URI}/references/checklist.md"]),
                    "sha256": hashlib.sha256(
                        self.downloads[f"{SKILL_URI}/references/checklist.md"]
                    ).hexdigest(),
                },
            ],
        }

    async def find_skills(self, query: str, **kwargs):
        del query, kwargs
        return {
            "skills": [
                {
                    "name": "shared",
                    "description": "agent copy",
                    "root_uri": "viking://agent/skills/shared",
                    "score": 0.99,
                },
                {
                    "name": "shared",
                    "description": "user copy",
                    "root_uri": "viking://user/u1/skills/shared",
                    "score": 0.5,
                },
                {
                    "name": "local-wins",
                    "description": "must be hidden",
                    "root_uri": "viking://user/u1/skills/local-wins",
                    "score": 1.0,
                },
            ]
        }

    async def get_skill(self, skill_name: str, **kwargs):
        del kwargs
        assert skill_name == "release-audit"
        self.get_skill_calls += 1
        return self.skill_result

    async def download_bytes(self, uri: str) -> bytes:
        self.download_calls.append(uri)
        return self.downloads[uri]

    async def read_content(self, uri: str, level: str = "read") -> str:
        assert level == "read"
        if uri == SKILL_MD_URI:
            return SKILL_MD
        return self.downloads[uri].decode("utf-8")

    async def close(self) -> None:
        self.closed = True


def _runtime(
    *,
    local_skill_names=(),
    request_id="request-1",
    sandbox_manager=None,
    storage_workspace=None,
    actor_peer_id="peer",
    openviking_connection=None,
    remote_skills=None,
    enable_cache=False,
):
    config = Config(
        ov_server={"server_url": "http://openviking.test"},
        storage_workspace=str(storage_workspace) if storage_workspace is not None else None,
        remote_skills=remote_skills or {},
    )
    sandbox_manager = sandbox_manager or _FakeSandboxManager()
    if enable_cache and not hasattr(sandbox_manager, "remote_skill_cache"):
        sandbox_manager.remote_skill_cache = RemoteSkillSnapshotCache(config)
    runtime = SkillRuntimeContext(
        config=config,
        session_key=SessionKey(type="cli", channel_id="default", chat_id="test"),
        sandbox_manager=sandbox_manager,
        workspace_id="workspace",
        openviking_connection=openviking_connection or {"api_key": "request-key"},
        actor_peer_id=actor_peer_id,
        local_skill_names=local_skill_names,
        request_id=request_id,
    )
    client = _FakeClient()
    runtime._client = client
    return runtime, client, sandbox_manager


@pytest.mark.asyncio
async def test_discovery_uses_user_scope_priority_and_hides_local_name():
    runtime, _client, _sandbox_manager = _runtime(local_skill_names={"local-wins"})

    candidates = await runtime.discover("audit")

    assert [(candidate.name, candidate.source) for candidate in candidates] == [
        ("shared", "openviking_user")
    ]
    summary = runtime.build_discovery_summary()
    assert "viking://user/u1/skills/shared/SKILL.md" in summary
    assert "openviking_multi_read" in summary
    assert "local-wins" not in summary


@pytest.mark.asyncio
async def test_activation_validates_skill_and_keeps_text_resources_remote():
    runtime, _client, sandbox_manager = _runtime()

    active = await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    runtime.note_remote_read(f"{SKILL_URI}/references/checklist.md")

    assert active is not None
    assert active.root_uri == SKILL_URI
    assert active.requires == {"bins": ["python3"], "env": []}
    assert runtime.materializations == {}
    assert sandbox_manager.sandbox.files == {}
    assert runtime.usage[-1]["event"] == "skill.resource.remote_read"


@pytest.mark.asyncio
async def test_registry_multi_read_activates_skill_and_renders_resource_bindings(monkeypatch):
    runtime, client, sandbox_manager = _runtime()
    tool = VikingMultiReadTool(config=runtime.config)

    async def get_client(_tool_context):
        return client

    monkeypatch.setattr(tool, "_get_client", get_client)
    registry = ToolRegistry(config=runtime.config)
    registry.register(tool)

    outcome = await registry.execute_detailed(
        "openviking_multi_read",
        {"uris": [SKILL_MD_URI]},
        session_key=runtime.session_key,
        sandbox_manager=sandbox_manager,
        skill_runtime=runtime,
    )

    assert runtime.activation_succeeded({"uris": [SKILL_MD_URI]}) is True
    assert SKILL_URI in runtime.active_skills
    assert "## OpenViking resource bindings" in outcome.result
    assert f"{SKILL_URI}/scripts/audit.py" in outcome.result
    assert "Run `python scripts/audit.py`." in outcome.result
    assert "Do not recreate, copy, or write them into the workspace" in outcome.result
    assert "VikingBot materializes and rewrites it automatically" in outcome.result

    prepared = await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})
    assert prepared.params["command"].endswith("/release-audit/scripts/audit.py")


@pytest.mark.asyncio
async def test_activation_rejects_manifest_uri_path_mismatch():
    runtime, client, _sandbox_manager = _runtime()
    client.skill_result["files"][2]["uri"] = f"{SKILL_URI}/scripts/other.py"

    with pytest.raises(SkillRuntimeError, match="canonical path"):
        await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)


@pytest.mark.asyncio
async def test_activation_rejects_definition_manifest_mismatch():
    runtime, client, _sandbox_manager = _runtime()
    client.skill_result["files"][0]["size"] += 1

    with pytest.raises(SkillRuntimeError, match="does not match its manifest"):
        await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)


@pytest.mark.asyncio
async def test_exec_materializes_bundle_and_rewrites_manifest_path():
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    prepared = await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    digest = hashlib.sha256(SKILL_URI.encode()).hexdigest()
    expected_root = f".remote-skill/request-1/{digest}/release-audit"
    assert prepared.params["command"] == f"python {expected_root}/scripts/audit.py"
    assert prepared.skill_uris == (SKILL_URI,)
    assert sandbox_manager.sandbox.files[f"{expected_root}/SKILL.md"] == SKILL_MD.encode()
    assert sandbox_manager.sandbox.files[f"{expected_root}/scripts/audit.py"] == b"print('ok')\n"
    assert (
        sandbox_manager.sandbox.files[f"{expected_root}/references/checklist.md"]
        == b"# Checklist\n"
    )


@pytest.mark.asyncio
async def test_registry_returns_real_skill_uri_and_resolved_args(monkeypatch):
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    registry = ToolRegistry(config=runtime.config)
    registry.register(ReadFileTool())
    info_calls = []
    monkeypatch.setattr(
        "vikingbot.agent.tools.registry.logger",
        SimpleNamespace(
            info=lambda *args: info_calls.append(args),
            warning=lambda *args: None,
            exception=lambda *args: None,
        ),
    )

    outcome = await registry.execute_detailed(
        "read_file",
        {"path": "references/checklist.md"},
        session_key=runtime.session_key,
        sandbox_manager=sandbox_manager,
        skill_runtime=runtime,
    )

    assert outcome.result == "# Checklist\n"
    assert outcome.skill_uris == (SKILL_URI,)
    assert outcome.effective_params["path"].endswith("/references/checklist.md")
    assert info_calls[0][0] == "[TOOL_PREPARED]: {}({})"
    assert info_calls[0][1] == "read_file"
    assert ".remote-skill/request-1/" in info_calls[0][2]


@pytest.mark.asyncio
async def test_multi_read_resolves_relative_resource_without_materializing():
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    prepared = await runtime.prepare_tool_call(
        SimpleNamespace(name="openviking_multi_read", resource_inputs={}),
        {"uris": ["references/checklist.md"]},
    )

    assert prepared.params["uris"] == [f"{SKILL_URI}/references/checklist.md"]
    assert prepared.skill_uris == (SKILL_URI,)
    assert sandbox_manager.sandbox.files == {}


@pytest.mark.asyncio
async def test_on_demand_materialization_checks_sandbox_requirements():
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    sandbox_manager.sandbox.requirements_ok = False

    with pytest.raises(SkillRuntimeError, match="SKILL_CAPABILITY_UNAVAILABLE"):
        await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert SKILL_URI in runtime.active_skills
    assert runtime.materializations == {}


@pytest.mark.asyncio
async def test_request_close_removes_materialized_snapshot():
    runtime, client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    await runtime.close()

    assert sandbox_manager.sandbox.removed == [".remote-skill/request-1"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_request_close_does_not_start_unused_sandbox():
    runtime, client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    await runtime.close()

    assert sandbox_manager.get_sandbox_calls == 0
    assert sandbox_manager.sandbox.removed == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_versioned_cache_reuses_downloaded_snapshot_across_agent_turns(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})
    await runtime1.close()
    assert sandbox_manager.sandbox.removed == [".remote-skill/request-1"]

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    prepared = await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client1.download_calls) == 2
    assert client2.download_calls == []
    assert "/request-2/" in prepared.params["command"]
    assert runtime1.usage[-1]["cache"] == "miss"
    assert runtime2.usage[-1]["cache"] == "hit"
    cache_root = tmp_path / "bot" / "remote_skill_cache"
    assert list(cache_root.rglob("metadata.json"))
    assert cache_root.stat().st_mode & 0o777 == 0o700
    assert next(cache_root.rglob("audit.py")).stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_cache_hit_rechecks_manifest_before_creating_execution_copy(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    client2.skill_result["revision"] = "revision-changed-after-activation"

    with pytest.raises(SkillRuntimeError, match="SKILL_REVISION_CHANGED"):
        await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert client2.download_calls == []
    assert runtime2.materializations == {}
    assert any(
        path.startswith(".remote-skill/request-2/") for path in sandbox_manager.sandbox.removed
    )


@pytest.mark.asyncio
async def test_versioned_cache_downloads_new_skill_revision(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    changed_script = b"print('new revision')\n"
    client2.downloads[f"{SKILL_URI}/scripts/audit.py"] = changed_script
    client2.skill_result["revision"] = "revision-2"
    script_entry = next(
        item for item in client2.skill_result["files"] if item["path"] == "scripts/audit.py"
    )
    script_entry["size"] = len(changed_script)
    script_entry["sha256"] = hashlib.sha256(changed_script).hexdigest()

    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client2.download_calls) == 2
    assert runtime2.usage[-1]["cache"] == "miss"
    assert len(list((tmp_path / "bot" / "remote_skill_cache").rglob("metadata.json"))) == 2


@pytest.mark.asyncio
async def test_versioned_cache_serializes_concurrent_first_download(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)

    await asyncio.gather(
        runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"}),
        runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"}),
    )

    assert len(client1.download_calls) + len(client2.download_calls) == 2
    assert sorted((runtime1.usage[-1]["cache"], runtime2.usage[-1]["cache"])) == [
        "hit",
        "miss",
    ]


@pytest.mark.asyncio
async def test_versioned_cache_does_not_share_across_principals(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        actor_peer_id="peer-1",
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        actor_peer_id="peer-2",
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client2.download_calls) == 2
    assert runtime2.usage[-1]["cache"] == "miss"


@pytest.mark.asyncio
async def test_versioned_cache_isolates_unresolved_api_key_identities(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        openviking_connection={"api_key": "key-one"},
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        openviking_connection={"api_key": "key-two"},
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client2.download_calls) == 2
    assert runtime2.usage[-1]["cache"] == "miss"


@pytest.mark.asyncio
async def test_versioned_cache_survives_api_key_rotation_for_same_user(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    stable_identity = {"account_id": "account", "user_id": "u1", "api_key_type": "user"}
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        openviking_connection={**stable_identity, "api_key": "key-one"},
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        openviking_connection={**stable_identity, "api_key": "key-two"},
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert client2.download_calls == []
    assert runtime2.usage[-1]["cache"] == "hit"


@pytest.mark.asyncio
async def test_versioned_cache_rebuilds_corrupt_snapshot(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})
    cache = sandbox_manager.remote_skill_cache
    cached_script = next(cache.root.rglob("audit.py"))
    cached_script.write_bytes(b"corrupt")

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client2.download_calls) == 2
    assert runtime2.usage[-1]["cache"] == "miss"


@pytest.mark.asyncio
async def test_versioned_cache_expires_after_idle_ttl(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    remote_settings = {"cache_idle_ttl_seconds": 0.01}
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        remote_skills=remote_settings,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})
    await asyncio.sleep(0.03)

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        remote_skills=remote_settings,
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client2.download_calls) == 2
    assert runtime2.usage[-1]["cache"] == "miss"


@pytest.mark.asyncio
async def test_versioned_cache_evicts_least_recently_used_entry_at_capacity(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    remote_settings = {"cache_max_entries": 1}

    async def materialize_revision(request_id, revision, script):
        runtime, client, _ = _runtime(
            request_id=request_id,
            sandbox_manager=sandbox_manager,
            storage_workspace=tmp_path,
            remote_skills=remote_settings,
            enable_cache=True,
        )
        client.downloads[f"{SKILL_URI}/scripts/audit.py"] = script
        client.skill_result["revision"] = revision
        script_entry = next(
            item for item in client.skill_result["files"] if item["path"] == "scripts/audit.py"
        )
        script_entry["size"] = len(script)
        script_entry["sha256"] = hashlib.sha256(script).hexdigest()
        await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
        await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})
        return client, runtime

    await materialize_revision("request-1", "revision-1", b"print('one')\n")
    await asyncio.sleep(0.01)
    await materialize_revision("request-2", "revision-2", b"print('two')\n")

    cache = sandbox_manager.remote_skill_cache
    assert len(list(cache.root.rglob("metadata.json"))) == 1

    client3, runtime3 = await materialize_revision("request-3", "revision-1", b"print('one')\n")
    assert len(client3.download_calls) == 2
    assert runtime3.usage[-1]["cache"] == "miss"


@pytest.mark.asyncio
async def test_versioned_cache_reclaims_key_locks_after_eviction(tmp_path):
    config = Config(
        ov_server={"server_url": "http://openviking.test"},
        storage_workspace=str(tmp_path),
        remote_skills={"cache_max_entries": 1},
    )
    cache = RemoteSkillSnapshotCache(config)

    async def build(snapshot: Path) -> SnapshotBuildResult:
        (snapshot / "SKILL.md").write_bytes(b"x")
        return SnapshotBuildResult(total_bytes=1, file_count=1)

    for index in range(100):
        key = RemoteSkillCacheKey(
            scope_digest=f"scope-{index}",
            root_digest="root",
            revision_digest="revision",
        )
        async with cache.acquire(
            key=key,
            root_uri=SKILL_URI,
            revision=f"revision-{index}",
            manifest_signature=f"manifest-{index}",
            builder=build,
        ):
            pass

    assert len(list(cache.root.rglob("metadata.json"))) == 1
    assert cache._key_locks == {}


@pytest.mark.asyncio
async def test_versioned_cache_enforces_total_byte_capacity(tmp_path):
    sandbox_manager = _FakeSandboxManager()
    remote_settings = {"cache_max_bytes": 1}
    runtime1, _client1, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        remote_skills=remote_settings,
        enable_cache=True,
    )
    await runtime1.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime1.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    cache = sandbox_manager.remote_skill_cache
    assert list(cache.root.rglob("metadata.json")) == []

    runtime2, client2, _ = _runtime(
        request_id="request-2",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        remote_skills=remote_settings,
        enable_cache=True,
    )
    await runtime2.activate_from_read(SKILL_MD_URI, SKILL_MD)
    await runtime2.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client2.download_calls) == 2
    assert runtime2.usage[-1]["cache"] == "miss"


@pytest.mark.asyncio
async def test_unavailable_cache_falls_back_to_request_materialization(tmp_path, monkeypatch):
    sandbox_manager = _FakeSandboxManager()
    runtime, client, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )

    def fail_cache_root():
        raise PermissionError("read-only cache root")

    monkeypatch.setattr(sandbox_manager.remote_skill_cache, "_prepare_root", fail_cache_root)
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    prepared = await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert len(client.download_calls) == 2
    assert runtime.usage[-1]["cache"] == "bypassed"
    assert "/request-1/" in prepared.params["command"]


@pytest.mark.asyncio
async def test_cache_prune_failure_does_not_block_materialization(tmp_path, monkeypatch):
    sandbox_manager = _FakeSandboxManager()
    runtime, _client, _ = _runtime(
        request_id="request-1",
        sandbox_manager=sandbox_manager,
        storage_workspace=tmp_path,
        enable_cache=True,
    )

    async def fail_prune():
        raise OSError("janitor unavailable")

    monkeypatch.setattr(sandbox_manager.remote_skill_cache, "prune", fail_prune)
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    prepared = await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert runtime.usage[-1]["cache"] == "miss"
    assert "/request-1/" in prepared.params["command"]


def test_allowed_tools_only_reduce_registry_definitions():
    runtime, _client, _sandbox_manager = _runtime()
    runtime.active_skills[SKILL_URI] = SimpleNamespace(
        root_uri=SKILL_URI,
        allowed_tools_declared=True,
        allowed_tools=("Read",),
    )
    registry = ToolRegistry(config=runtime.config)
    registry.register(ReadFileTool())
    registry.register(ExecTool())

    definitions = registry.get_definitions(skill_runtime=runtime)

    assert [definition["function"]["name"] for definition in definitions] == ["read_file"]


@pytest.mark.asyncio
async def test_remote_skill_activation_reuses_experience_recall(monkeypatch):
    hook = OpenVikingPostCallHook()

    async def _fake_search(*args, **kwargs):
        del args, kwargs
        return "Prefer dry-run first."

    monkeypatch.setattr(hook, "_search_skill_experiences", _fake_search)
    result = await hook.execute(
        HookContext(
            event_type="tool.post_call",
            workspace_id="workspace",
            session_key=SessionKey(type="cli", channel_id="default", chat_id="test"),
            config=Config(),
        ),
        "openviking_multi_read",
        {"uris": [SKILL_MD_URI]},
        f"--- START OF {SKILL_MD_URI} ---\n{SKILL_MD}",
    )

    assert "## Related Experiences" in result["result"]
    assert "Prefer dry-run first." in result["result"]


@pytest.mark.asyncio
async def test_absolute_workspace_path_is_not_reinterpreted_as_skill_resource():
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    prepared = await runtime.prepare_tool_call(ReadFileTool(), {"path": "/workspace/existing.txt"})

    assert prepared.params["path"] == "/workspace/existing.txt"
    assert prepared.skill_uris == (SKILL_URI,)
    assert runtime.materializations == {}
    assert sandbox_manager.sandbox.files == {}


@pytest.mark.asyncio
async def test_relative_workspace_path_requires_explicit_workspace_prefix():
    runtime, _client, _sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    with pytest.raises(SkillRuntimeError, match="SKILL_RESOURCE_NOT_FOUND"):
        await runtime.prepare_tool_call(ReadFileTool(), {"path": "output/generated-report.md"})

    prepared = await runtime.prepare_tool_call(
        ReadFileTool(), {"path": "workspace:output/generated-report.md"}
    )

    assert prepared.params["path"] == "output/generated-report.md"
    assert runtime.materializations == {}


@pytest.mark.asyncio
async def test_missing_relative_skill_resource_fails_closed():
    runtime, _client, _sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    with pytest.raises(SkillRuntimeError, match="SKILL_RESOURCE_NOT_FOUND"):
        await runtime.prepare_tool_call(ReadFileTool(), {"path": "references/missing.md"})
    with pytest.raises(SkillRuntimeError, match="SKILL_RESOURCE_NOT_FOUND"):
        await runtime.prepare_tool_call(ExecTool(), {"command": "python missing.py"})


@pytest.mark.asyncio
async def test_unactivated_skill_auxiliary_resource_is_blocked():
    runtime, _client, _sandbox_manager = _runtime()

    with pytest.raises(SkillRuntimeError, match="SKILL_NOT_ACTIVE"):
        await runtime.prepare_tool_call(
            SimpleNamespace(name="openviking_multi_read", resource_inputs={}),
            {"uris": [f"{SKILL_URI}/references/checklist.md"]},
        )


@pytest.mark.asyncio
async def test_multiple_skills_intersect_policy_without_model_selected_binding():
    runtime, _client, _sandbox_manager = _runtime()
    read_skill = await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    second_uri = "viking://user/u1/skills/python-helper"
    second_skill = replace(
        read_skill,
        name="python-helper",
        root_uri=second_uri,
        skill_md_uri=f"{second_uri}/SKILL.md",
        files={},
        allowed_tools=("Read", "Bash(python *)"),
        requires={"bins": [], "env": []},
    )
    runtime.active_skills[second_uri] = second_skill
    registry = ToolRegistry(config=runtime.config)
    registry.register(ReadFileTool())
    registry.register(ExecTool())

    definitions = {
        definition["function"]["name"]: definition
        for definition in registry.get_definitions(skill_runtime=runtime)
    }
    assert set(definitions) == {"read_file", "exec"}
    assert (
        "_remote_skill_uri" not in definitions["read_file"]["function"]["parameters"]["properties"]
    )
    assert "_remote_skill_uri" not in definitions["exec"]["function"]["parameters"]["properties"]

    with pytest.raises(SkillRuntimeError, match="SKILL_TOOL_NOT_ALLOWED"):
        await runtime.prepare_tool_call(ExecTool(), {"command": "git status"})

    prepared = await runtime.prepare_tool_call(ExecTool(), {"command": "python -c 'print(1)'"})
    assert prepared.params == {"command": "python -c 'print(1)'"}
    assert prepared.skill_uris == (SKILL_URI, second_uri)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "python scripts/audit.py; rm -rf output",
        "python scripts/audit.py && rm -rf output",
        "python scripts/audit.py | tee output.txt",
        "python scripts/$(echo audit).py",
        "python `echo scripts/audit.py`",
    ],
)
async def test_constrained_bash_rejects_compound_shell_syntax(command):
    runtime, _client, _sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)

    with pytest.raises(SkillRuntimeError, match="SKILL_TOOL_NOT_ALLOWED"):
        await runtime.prepare_tool_call(ExecTool(), {"command": command})


@pytest.mark.asyncio
async def test_registry_logs_skill_policy_rejection_without_exception_trace(monkeypatch):
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    registry = ToolRegistry(config=runtime.config)
    registry.register(ExecTool())
    warning_calls = []
    exception_calls = []
    monkeypatch.setattr(
        "vikingbot.agent.tools.registry.logger",
        SimpleNamespace(
            warning=lambda *args: warning_calls.append(args),
            exception=lambda *args: exception_calls.append(args),
        ),
    )

    outcome = await registry.execute_detailed(
        "exec",
        {"command": "mkdir -p scripts"},
        session_key=runtime.session_key,
        sandbox_manager=sandbox_manager,
        skill_runtime=runtime,
    )

    assert "SKILL_TOOL_NOT_ALLOWED" in str(outcome.result)
    assert warning_calls
    assert exception_calls == []


def test_multiple_skills_hide_tools_not_allowed_by_every_skill():
    runtime, _client, _sandbox_manager = _runtime()
    runtime.active_skills[SKILL_URI] = SimpleNamespace(
        root_uri=SKILL_URI,
        allowed_tools_declared=True,
        allowed_tools=("Read", "Bash"),
    )
    other_uri = "viking://user/u1/skills/read-only"
    runtime.active_skills[other_uri] = SimpleNamespace(
        root_uri=other_uri,
        allowed_tools_declared=True,
        allowed_tools=("Read",),
    )
    registry = ToolRegistry(config=runtime.config)
    registry.register(ReadFileTool())
    registry.register(ExecTool())

    definitions = registry.get_definitions(skill_runtime=runtime)

    assert [definition["function"]["name"] for definition in definitions] == ["read_file"]


@pytest.mark.asyncio
async def test_requirements_are_checked_before_pure_tool_calls():
    runtime, _client, sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    sandbox_manager.sandbox.requirements_ok = False

    with pytest.raises(SkillRuntimeError, match="bin:python3"):
        await runtime.prepare_tool_call(ReadFileTool(), {"path": "/workspace/existing.txt"})

    assert runtime.materializations == {}


@pytest.mark.asyncio
async def test_nested_resource_inputs_are_materialized():
    runtime, _client, _sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    tool = SimpleNamespace(
        name="read_file",
        resource_inputs={"/files/*/workspace_path": "local_file"},
    )

    prepared = await runtime.prepare_tool_call(
        tool,
        {"files": [{"workspace_path": f"{SKILL_URI}/scripts/audit.py"}]},
    )

    assert prepared.params["files"][0]["workspace_path"].endswith("/scripts/audit.py")
    assert prepared.skill_uris == (SKILL_URI,)


@pytest.mark.asyncio
async def test_materialization_rejects_revision_change_with_same_file_names():
    runtime, client, _sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    client.skill_result["revision"] = "revision-2"

    with pytest.raises(SkillRuntimeError, match="SKILL_REVISION_CHANGED"):
        await runtime.prepare_tool_call(ExecTool(), {"command": "python scripts/audit.py"})

    assert runtime.materializations == {}


@pytest.mark.asyncio
async def test_activation_requires_content_addressed_manifest():
    runtime, client, _sandbox_manager = _runtime()
    client.skill_result.pop("revision")

    with pytest.raises(SkillRuntimeError, match="SKILL_INTEGRITY_UNAVAILABLE"):
        await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)


def test_sandbox_command_failure_parser_rejects_nonzero_exit_code():
    with pytest.raises(SandboxExecutionError, match="binary decode failed"):
        SandboxBackend._ensure_command_succeeded("STDERR:\nfailed\nExit code: 1", "binary decode")

    SandboxBackend._ensure_command_succeeded("(no output)", "binary decode")


def test_sandbox_manager_owns_private_host_skill_cache(tmp_path):
    config = Config(storage_workspace=str(tmp_path / "data"))
    manager = SandboxManager(
        config,
        sandbox_parent_path=tmp_path / "sandboxes",
        source_workspace_path=tmp_path / "source",
    )

    assert manager.remote_skill_cache.root == tmp_path / "data" / "bot" / "remote_skill_cache"
    assert manager.remote_skill_cache.root != manager.workspace
    assert manager.remote_skill_cache.root.exists() is False


def test_runtime_enablement_follows_visible_multi_read_tool(tmp_path):
    agent = AgentLoop.__new__(AgentLoop)
    agent.config = Config(ov_server={"server_url": "http://openviking.test"})
    agent.tools = ToolRegistry(config=agent.config)
    agent.tools.register(SimpleNamespace(name="openviking_multi_read"))
    agent.sandbox_manager = None
    session_key = SessionKey(type="cli", channel_id="default", chat_id="test")

    enabled = agent._create_skill_runtime(
        session_key=session_key,
        workspace=tmp_path,
        ov_tools_enable=True,
        disabled_tools=None,
        openviking_connection=None,
        actor_peer_id="peer",
    )
    disabled = agent._create_skill_runtime(
        session_key=session_key,
        workspace=tmp_path,
        ov_tools_enable=True,
        disabled_tools=["openviking_multi_read"],
        openviking_connection=None,
        actor_peer_id="peer",
    )

    assert enabled is not None
    assert disabled is None


@pytest.mark.asyncio
async def test_agent_loop_defers_same_batch_consumers_until_refreshed_schema(tmp_path, monkeypatch):
    event_order = []

    class _Provider(LLMProvider):
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            del args, kwargs
            self.calls += 1
            if self.calls == 2:
                return LLMResponse(
                    content="read after activation",
                    tool_calls=[
                        ToolCallRequest(
                            id="read-2",
                            name="read_file",
                            arguments={"path": "references/checklist.md"},
                            tokens=0,
                        )
                    ],
                )
            return LLMResponse(
                content="activate then read",
                tool_calls=[
                    ToolCallRequest(
                        id="read-1",
                        name="read_file",
                        arguments={"path": "references/checklist.md"},
                        tokens=0,
                    ),
                    ToolCallRequest(
                        id="activate-1",
                        name="openviking_multi_read",
                        arguments={"uris": [SKILL_MD_URI]},
                        tokens=0,
                    ),
                ],
            )

        def get_default_model(self) -> str:
            return "fake-model"

    class _Runtime:
        active = False

        def is_activation_call(self, name, params):
            return name == "openviking_multi_read" and SKILL_MD_URI in params["uris"]

        def activation_succeeded(self, params):
            del params
            return self.active

    class _Registry:
        def __init__(self, runtime):
            self.runtime = runtime
            self.execution_order = []

        def get_definitions(self, **kwargs):
            del kwargs
            return [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in ("read_file", "openviking_multi_read")
            ]

        async def execute_detailed(self, name, params, **kwargs):
            del kwargs
            event_order.append(f"execute:{name}")
            self.execution_order.append(name)
            if name == "openviking_multi_read":
                self.runtime.active = True
                result = "activated"
            else:
                assert self.runtime.active is True
                result = "checklist"
            return SimpleNamespace(
                result=result,
                effective_params=params,
                skill_uris=(),
            )

    runtime = _Runtime()
    registry = _Registry(runtime)
    monkeypatch.setattr(
        "vikingbot.agent.loop.logger",
        SimpleNamespace(
            debug=lambda *args: None,
            error=lambda *args: None,
            exception=lambda *args: None,
            info=lambda message, *args: event_order.append(str(message)),
            warning=lambda *args: None,
        ),
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=tmp_path,
        config=Config(storage_workspace=str(tmp_path)),
        max_iterations=2,
    )
    captured_turns = []

    await loop._run_agent_loop(
        messages=[{"role": "user", "content": "run the skill"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="activation"),
        publish_events=False,
        tool_registry=registry,
        skill_runtime=runtime,
        captured_turns=captured_turns,
        inject_write_experience=False,
        allow_final_fallback=False,
    )

    assert registry.execution_order == ["openviking_multi_read", "read_file"]
    first_tool_log = next(
        index for index, event in enumerate(event_order) if event.startswith("[TOOL_CALL]:")
    )
    first_execution = next(
        index for index, event in enumerate(event_order) if event.startswith("execute:")
    )
    first_result_log = next(
        index for index, event in enumerate(event_order) if event.startswith("[RESULT]:")
    )
    assert first_tool_log < first_execution < first_result_log
    assert [item["tool_name"] for item in captured_turns[0]["tool_calls"]] == [
        "read_file",
        "openviking_multi_read",
    ]
    assert captured_turns[0]["tool_calls"][0]["result"].startswith("Error: SKILL_CONTEXT_UPDATED")
    assert captured_turns[1]["tool_calls"][0]["result"] == "checklist"


@pytest.mark.asyncio
async def test_tool_result_event_publishes_full_remote_skill_content(tmp_path):
    skill_content = "full-remote-skill-content"

    class _Provider(LLMProvider):
        async def chat(self, *args, **kwargs):
            del args, kwargs
            return LLMResponse(
                content="read skill",
                tool_calls=[
                    ToolCallRequest(
                        id="read-secret",
                        name="openviking_multi_read",
                        arguments={"uris": [SKILL_MD_URI]},
                        tokens=0,
                    )
                ],
            )

        def get_default_model(self) -> str:
            return "fake-model"

    class _Runtime:
        def is_activation_call(self, name, params):
            del params
            return name == "openviking_multi_read"

        def activation_succeeded(self, params):
            del params
            return True

    class _Registry:
        def get_definitions(self, **kwargs):
            del kwargs
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "openviking_multi_read",
                        "description": "read",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        async def execute_detailed(self, name, params, **kwargs):
            del name, kwargs
            return SimpleNamespace(
                result=f"SKILL.md contains {skill_content}",
                effective_params=params,
                skill_uris=(SKILL_URI,),
            )

    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=_Provider(),
        workspace=tmp_path,
        config=Config(storage_workspace=str(tmp_path)),
        max_iterations=1,
    )

    await loop._run_agent_loop(
        messages=[{"role": "user", "content": "read the skill"}],
        session_key=SessionKey(type="cli", channel_id="default", chat_id="privacy"),
        publish_events=True,
        tool_registry=_Registry(),
        skill_runtime=_Runtime(),
        inject_write_experience=False,
        allow_final_fallback=False,
    )

    events = [await bus.consume_outbound() for _ in range(bus.outbound.qsize())]
    result_events = [event for event in events if event.event_type == OutboundEventType.TOOL_RESULT]
    assert len(result_events) == 1
    assert skill_content in result_events[0].content


@pytest.mark.asyncio
async def test_materialized_skill_read_returns_full_result():
    runtime, _client, _sandbox_manager = _runtime()
    await runtime.activate_from_read(SKILL_MD_URI, SKILL_MD)
    registry = ToolRegistry(config=runtime.config)
    registry.register(ReadFileTool())

    outcome = await registry.execute_detailed(
        "read_file",
        {"path": f"{SKILL_URI}/scripts/audit.py"},
        session_key=runtime.session_key,
        sandbox_manager=runtime.sandbox_manager,
        skill_runtime=runtime,
    )

    assert "print('ok')" in outcome.result
    assert outcome.skill_uris == (SKILL_URI,)


@pytest.mark.asyncio
async def test_add_resource_uploads_materialized_sandbox_file(monkeypatch):
    _runtime_value, _client, sandbox_manager = _runtime()
    sandbox_manager.sandbox.files["assets/template.bin"] = b"binary-template"
    captured = {}

    class _UploadClient:
        async def add_resource(self, path, description, *, to=None):
            captured["content"] = Path(path).read_bytes()
            captured["description"] = description
            captured["to"] = to
            return {"root_uri": "viking://user/u1/resources/template"}

    tool = VikingAddResourceTool()

    async def _get_client(_context):
        return _UploadClient()

    async def _release_client(_context, _client_value):
        return None

    monkeypatch.setattr(tool, "_get_client", _get_client)
    monkeypatch.setattr(tool, "_release_client", _release_client)
    result = await tool.execute(
        ToolContext(
            session_key=_runtime_value.session_key,
            sandbox_manager=sandbox_manager,
        ),
        path="assets/template.bin",
        description="template",
    )

    assert result.startswith("Successfully added resource")
    assert captured == {
        "content": b"binary-template",
        "description": "template",
        "to": None,
    }
    assert sandbox_manager.sandbox.exported == ["assets/template.bin"]


@pytest.mark.asyncio
async def test_viking_client_preserves_lightweight_get_skill_default():
    captured = {}

    class _Client:
        async def get_skill(self, skill_name, **kwargs):
            captured["skill_name"] = skill_name
            captured.update(kwargs)
            return {"name": skill_name}

    wrapper = VikingClient.__new__(VikingClient)
    wrapper.client = _Client()

    await wrapper.get_skill("release-audit", target_uri="viking://user/u1/skills")

    assert captured["include_integrity"] is False


@pytest.mark.asyncio
async def test_viking_client_forwards_explicit_integrity_request():
    captured = {}

    class _Client:
        async def get_skill(self, skill_name, **kwargs):
            captured["skill_name"] = skill_name
            captured.update(kwargs)
            return {"name": skill_name}

    wrapper = VikingClient.__new__(VikingClient)
    wrapper.client = _Client()

    await wrapper.get_skill(
        "release-audit",
        target_uri="viking://user/u1/skills",
        include_integrity=True,
    )

    assert captured["include_integrity"] is True


@pytest.mark.asyncio
async def test_aiosandbox_reads_binary_files_through_download_stream(tmp_path):
    captured = {}

    class _Files:
        async def download_file(self, *, path):
            captured["path"] = path
            yield b"binary-"
            yield b"payload"

    backend = AioSandboxBackend.__new__(AioSandboxBackend)
    backend._client = SimpleNamespace(file=_Files())
    backend._workspace = tmp_path

    assert await backend.read_file_bytes("assets/template.bin") == b"binary-payload"
    assert captured["path"] == "/home/gem/assets/template.bin"


@pytest.mark.asyncio
async def test_aiosandbox_writes_binary_files_with_base64_encoding(tmp_path):
    captured = {}

    class _Files:
        async def write_file(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(success=True, message=None)

    backend = AioSandboxBackend.__new__(AioSandboxBackend)
    backend._client = SimpleNamespace(file=_Files())
    backend._workspace = tmp_path

    await backend.write_file_bytes("assets/template.bin", b"\x00\xff")

    assert captured == {
        "file": "/home/gem/assets/template.bin",
        "content": "AP8=",
        "encoding": "base64",
    }


@pytest.mark.asyncio
async def test_opensandbox_reads_binary_files_through_vke_sdk(tmp_path):
    captured = {}

    class _Files:
        async def read_bytes_stream(self, path, *, range_header):
            captured["path"] = path
            captured["range_header"] = range_header

            async def chunks():
                yield b"binary-"
                yield b"payload"

            return chunks()

    backend = OpenSandboxBackend.__new__(OpenSandboxBackend)
    backend._sandbox = SimpleNamespace(files=_Files())
    backend._workspace = tmp_path
    backend._is_vke = True

    assert await backend.read_file_bytes("assets/template.bin") == b"binary-payload"
    assert captured["path"] == "/workspace/assets/template.bin"
    assert captured["range_header"] is None
