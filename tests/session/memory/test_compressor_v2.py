# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test for SessionCompressorV2.

Uses MockVikingFS and real VLM (from config).
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session import compressor_v2 as compressor_v2_module
from openviking.session.compressor_v2 import SessionCompressorV2
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import RoleScope
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.merge_op.base import StrPatch
from openviking.session.memory.merge_op.patch import PatchOp
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.versioning import content_digest
from openviking.telemetry import OperationTelemetry, bind_telemetry
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config, initialize_openviking_config

# Let openviking logger propagate to pytest
for logger_name in ["openviking", "openviking.session.memory"]:
    logger = logging.getLogger(logger_name)
    logger.propagate = True
    logger.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


def test_plain_string_patch_conversion_makes_tool_memory_merge_safe():
    schema = MemoryTypeSchema(
        memory_type="tools",
        fields=[
            MemoryField(
                name="tool_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(
                name="guidelines",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )
    registry = SimpleNamespace(get=lambda memory_type: schema if memory_type == "tools" else None)
    old_file = MemoryFile(
        uri="viking://agent/default/memories/tools/search.md",
        memory_type="tools",
        extra_fields={"guidelines": "## Guidelines\n- Prefer specific queries.\n"},
    )
    operation = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="tools",
        uris=["viking://agent/default/memories/tools/search.md"],
        memory_fields={
            "tool_name": "search",
            "guidelines": "## Guidelines\n- Prefer specific queries.\n- Add source qualifiers.\n",
        },
    )
    operations = ResolvedOperations(
        upsert_operations=[operation],
        delete_file_contents=[],
        errors=[],
    )

    conversions = compressor_v2_module._convert_plain_string_patches_to_structured(
        operations,
        registry,
    )

    assert conversions == [
        {
            "uri": "viking://agent/default/memories/tools/search.md",
            "memory_type": "tools",
            "field": "guidelines",
        }
    ]
    assert isinstance(operation.memory_fields["guidelines"], StrPatch)
    latest_guidelines = "## Guidelines\n- Prefer specific queries.\n- Keep user locale.\n"
    assert (
        PatchOp(FieldType.STRING).apply(latest_guidelines, operation.memory_fields["guidelines"])
        == "## Guidelines\n- Prefer specific queries.\n- Add source qualifiers.\n"
        "- Keep user locale.\n"
    )
    assert (
        compressor_v2_module._operation_conflict_reason(operation, registry) != "plain_string_patch"
    )


def test_operation_exact_lock_uris_include_deleted_link_endpoints():
    exp_uri = "viking://agent/default/memories/experiences/old.md"
    traj_uri = "viking://agent/default/memories/trajectories/t1.md"
    operations = ResolvedOperations(
        upsert_operations=[],
        delete_file_contents=[
            MemoryFile(
                uri=exp_uri,
                links=[
                    {
                        "from_uri": exp_uri,
                        "to_uri": traj_uri,
                        "link_type": "derived_from",
                    }
                ],
            )
        ],
        errors=[],
    )

    lock_uris = compressor_v2_module._collect_operation_lock_uris(operations)

    assert exp_uri in lock_uris
    assert traj_uri in lock_uris
    assert "viking://agent/default/memories/experiences/.overview.md" in lock_uris
    assert "viking://agent/default/memories/trajectories/.overview.md" in lock_uris


@pytest.mark.asyncio
async def test_resolve_supersedes_consumes_field_only_after_resolved():
    compressor = SessionCompressorV2(vikingdb=None)
    exp_dir = "viking://agent/default/memories/experiences"
    old_uri = f"{exp_dir}/old.md"
    traj_uri = "viking://agent/default/memories/trajectories/t1.md"
    old_file = MemoryFile(
        uri=old_uri,
        memory_type="experiences",
        links=[
            {
                "from_uri": old_uri,
                "to_uri": traj_uri,
                "link_type": "derived_from",
            }
        ],
    )
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type="experiences",
                uris=[f"{exp_dir}/new.md"],
                memory_fields={"experience_name": "new", "supersedes": "old"},
            )
        ],
        delete_file_contents=[],
        errors=[],
    )

    class FakeVikingFS:
        async def read_file(self, uri: str, ctx=None):
            assert uri == old_uri
            return MemoryFileUtils.write(old_file)

    provider = SimpleNamespace(_render_experience_dir=lambda _ctx: exp_dir)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    inheritance_map = await compressor._resolve_supersedes(
        operations,
        ctx,
        FakeVikingFS(),
        provider,
    )

    assert operations.errors == []
    assert operations.delete_file_contents[0].uri == old_uri
    assert operations.upsert_operations[0].memory_fields == {"experience_name": "new"}
    assert inheritance_map == {f"{exp_dir}/new.md": [traj_uri]}


@pytest.mark.asyncio
async def test_resolve_supersedes_retries_when_prefetched_target_disappears():
    compressor = SessionCompressorV2(vikingdb=None)
    exp_dir = "viking://agent/default/memories/experiences"
    old_uri = f"{exp_dir}/old.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type="experiences",
                uris=[f"{exp_dir}/new.md"],
                memory_fields={"experience_name": "new", "supersedes": "old"},
            )
        ],
        delete_file_contents=[],
        errors=[],
    )

    class FakeVikingFS:
        async def read_file(self, uri: str, ctx=None):
            assert uri == old_uri
            raise FileNotFoundError(uri)

    provider = SimpleNamespace(
        _render_experience_dir=lambda _ctx: exp_dir,
        prefetched_uris=[old_uri],
        read_file_versions={old_uri: "base-digest"},
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with pytest.raises(compressor_v2_module.OperationExactVersionConflict) as exc_info:
        await compressor._resolve_supersedes(operations, ctx, FakeVikingFS(), provider)

    assert exc_info.value.conflicts == [old_uri]
    assert operations.delete_file_contents == []
    assert operations.errors == []
    assert operations.upsert_operations[0].memory_fields["supersedes"] == "old"


@pytest.mark.asyncio
async def test_resolve_supersedes_unresolved_target_marks_operations_invalid():
    compressor = SessionCompressorV2(vikingdb=None)
    exp_dir = "viking://agent/default/memories/experiences"
    old_uri = f"{exp_dir}/missing.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type="experiences",
                uris=[f"{exp_dir}/new.md"],
                memory_fields={"experience_name": "new", "supersedes": "missing"},
            )
        ],
        delete_file_contents=[],
        errors=[],
    )

    class FakeVikingFS:
        async def read_file(self, uri: str, ctx=None):
            assert uri == old_uri
            raise FileNotFoundError(uri)

    provider = SimpleNamespace(_render_experience_dir=lambda _ctx: exp_dir)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    inheritance_map = await compressor._resolve_supersedes(
        operations,
        ctx,
        FakeVikingFS(),
        provider,
    )

    assert inheritance_map == {}
    assert operations.delete_file_contents == []
    assert len(operations.errors) == 1
    assert "failed to resolve" in operations.errors[0]
    assert operations.upsert_operations[0].memory_fields["supersedes"] == "missing"


@pytest.mark.asyncio
async def test_replace_string_update_can_be_normalized_to_patch_and_replayed():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(
                name="experience_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.REPLACE,
            ),
        ],
    )
    registry = SimpleNamespace(
        get=lambda memory_type: schema if memory_type == "experiences" else None
    )
    exp_uri = "viking://agent/default/memories/experiences/debug.md"
    old_content = "## Situation\n- old\n"
    stale_replacement = "## Situation\n- old\n- learned from stale read\n"
    latest_content = "## Situation\n- old\n- concurrent update\n"
    old_file = MemoryFile(
        uri=exp_uri,
        memory_type="experiences",
        content=old_content,
        extra_fields={"experience_name": "debug"},
    )
    operation = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="experiences",
        uris=[exp_uri],
        memory_fields={
            "experience_name": "debug",
            "content": stale_replacement,
        },
    )
    operations = ResolvedOperations(
        upsert_operations=[operation],
        delete_file_contents=[],
        errors=[],
    )

    conversions = compressor_v2_module._convert_plain_string_patches_to_structured(
        operations,
        registry,
    )

    assert conversions == [
        {
            "uri": exp_uri,
            "memory_type": "experiences",
            "field": "content",
        }
    ]
    assert isinstance(operation.memory_fields["content"], StrPatch)
    assert compressor_v2_module._operation_conflict_reason(operation, registry) == ""

    class FakeVikingFS:
        def __init__(self):
            self.files = {
                exp_uri: MemoryFileUtils.write(
                    MemoryFile(
                        uri=exp_uri,
                        memory_type="experiences",
                        content=latest_content,
                        extra_fields={"experience_name": "debug"},
                    )
                )
            }

        async def read_file(self, uri: str, ctx=None):
            return self.files.get(uri, "")

        async def write_file(self, uri: str, content: str, ctx=None):
            self.files[uri] = content

    fake_fs = FakeVikingFS()
    updater = MemoryUpdater(registry=registry)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.session.memory.memory_updater.get_viking_fs", return_value=fake_fs),
        patch.object(updater, "generate_overview", new_callable=AsyncMock),
    ):
        result = await updater.apply_operations(operations, ctx)

    assert result.edited_uris == [exp_uri]
    final = MemoryFileUtils.read(await fake_fs.read_file(exp_uri), uri=exp_uri)
    assert final.content == "## Situation\n- old\n- learned from stale read\n- concurrent update"


@pytest.mark.asyncio
async def test_operation_exact_apply_window_batches_followers():
    unique_path = "viking://agent/default/memories/experiences/window_test.md"
    events: List[str] = []

    handle = SimpleNamespace(id="window-handle", locks=[])
    lock_manager = SimpleNamespace(
        create_handle=Mock(return_value=handle),
        acquire_exact_path_batch=AsyncMock(return_value=True),
        release=AsyncMock(),
    )

    async def worker(label: str):
        async def apply_func(lock_handle):
            events.append(f"apply:{label}:{lock_handle.id}")
            return label

        return await compressor_v2_module._enqueue_operation_exact_apply_window(
            lock_manager=lock_manager,
            window_key_paths=[unique_path],
            lock_paths=[unique_path],
            window_seconds=0.01,
            phase_metric_key="experience_single",
            apply_func=apply_func,
        )

    telemetry = OperationTelemetry(operation="session.commit", enabled=True)
    with bind_telemetry(telemetry):
        results = await asyncio.gather(worker("first"), worker("second"))

    assert results == ["first", "second"]
    assert events == ["apply:first:window-handle", "apply:second:window-handle"]
    lock_manager.create_handle.assert_called_once()
    lock_manager.acquire_exact_path_batch.assert_awaited_once_with(
        handle,
        [unique_path],
        timeout=None,
    )
    lock_manager.release.assert_awaited_once_with(handle)


@pytest.mark.asyncio
async def test_operation_exact_apply_window_batches_overlapping_targets():
    shared_path = "viking://agent/default/memories/experiences/shared.md"
    first_path = "viking://agent/default/memories/experiences/first.md"
    second_path = "viking://agent/default/memories/experiences/second.md"
    events: List[str] = []

    handle = SimpleNamespace(id="window-handle", locks=[])
    lock_manager = SimpleNamespace(
        create_handle=Mock(return_value=handle),
        acquire_exact_path_batch=AsyncMock(return_value=True),
        release=AsyncMock(),
    )

    async def worker(label: str, paths: list[str]):
        async def apply_func(lock_handle):
            events.append(f"apply:{label}:{lock_handle.id}")
            return label

        return await compressor_v2_module._enqueue_operation_exact_apply_window(
            lock_manager=lock_manager,
            window_key_paths=paths,
            lock_paths=paths,
            window_seconds=0.01,
            phase_metric_key="experience_single",
            apply_func=apply_func,
        )

    telemetry = OperationTelemetry(operation="session.commit", enabled=True)
    with bind_telemetry(telemetry):
        results = await asyncio.gather(
            worker("first", [first_path, shared_path]),
            worker("second", [shared_path, second_path]),
        )

    assert results == ["first", "second"]
    assert events == ["apply:first:window-handle", "apply:second:window-handle"]
    lock_manager.create_handle.assert_called_once()
    lock_manager.acquire_exact_path_batch.assert_awaited_once_with(
        handle,
        [first_path, shared_path, second_path],
        timeout=None,
    )
    lock_manager.release.assert_awaited_once_with(handle)


class MockVikingFS:
    """Mock VikingFS for testing with unified memory storage."""

    def __init__(self):
        # Unified storage: key is URI, value is dict with type and content/children
        self._store: Dict[str, Dict[str, Any]] = {}
        self._snapshot: Dict[str, str] = {}

    def _uri_to_path(self, uri: str, ctx=None) -> str:
        """Mock _uri_to_path method for testing."""
        # For testing purposes, we'll just return the URI as-is
        return uri

    def _get_parent_uri(self, uri: str) -> str:
        """Get parent directory URI."""
        # Handle URIs like "viking://agent/default/memories/cards/file.md"
        parts = uri.split("/")
        if len(parts) <= 3:
            return uri  # Root or protocol level
        return "/".join(parts[:-1])

    def _get_name_from_uri(self, uri: str) -> str:
        """Get file/directory name from URI."""
        parts = uri.split("/")
        return parts[-1] if parts else ""

    async def read_file(self, uri: str, **kwargs) -> str:
        """Mock read_file."""
        entry = self._store.get(uri)
        if entry and entry.get("type") == "file":
            return entry.get("content", "")
        return ""

    async def write_file(self, uri: str, content: str, **kwargs) -> None:
        """Mock write_file - automatically updates parent directory entries."""
        # Create parent directories if they don't exist
        parent_uri = self._get_parent_uri(uri)
        if parent_uri and parent_uri != uri:
            await self.mkdir(parent_uri)

        # Write the file
        self._store[uri] = {"type": "file", "content": content}

        # Update parent directory's entries
        if parent_uri and parent_uri in self._store:
            name = self._get_name_from_uri(uri)
            # Create entry for this file in parent's children
            file_entry = {
                "name": name,
                "isDir": False,
                "uri": uri,
                "abstract": content[:100] if content else "",
            }
            # Update or add to parent's children
            parent = self._store[parent_uri]
            if "children" not in parent:
                parent["children"] = []
            # Remove existing entry if present
            parent["children"] = [c for c in parent["children"] if c.get("name") != name]
            parent["children"].append(file_entry)

    async def ls(self, uri: str, **kwargs) -> List[Dict[str, Any]]:
        """Mock ls - returns entries from unified storage."""
        entry = self._store.get(uri)
        if entry and entry.get("type") == "dir":
            return entry.get("children", [])
        return []

    async def mkdir(self, uri: str, **kwargs) -> None:
        """Mock mkdir - recursively creates parent directories."""
        if uri in self._store:
            return  # Already exists

        # Create parent directories first
        parent_uri = self._get_parent_uri(uri)
        if parent_uri and parent_uri != uri:
            await self.mkdir(parent_uri)

        # Create this directory
        self._store[uri] = {"type": "dir", "children": []}

        # Update parent directory's entries
        if parent_uri and parent_uri in self._store:
            name = self._get_name_from_uri(uri)
            dir_entry = {"name": name, "isDir": True, "uri": uri}
            parent = self._store[parent_uri]
            # Remove existing entry if present
            parent["children"] = [c for c in parent.get("children", []) if c.get("name") != name]
            parent["children"].append(dir_entry)

    async def rm(self, uri: str, **kwargs) -> None:
        """Mock rm - removes file and updates parent directory."""
        if uri not in self._store:
            return

        # Remove from parent's children
        parent_uri = self._get_parent_uri(uri)
        name = self._get_name_from_uri(uri)
        if parent_uri and parent_uri in self._store:
            parent = self._store[parent_uri]
            parent["children"] = [c for c in parent.get("children", []) if c.get("name") != name]

        # Remove the file/directory
        del self._store[uri]

    async def stat(self, uri: str, **kwargs) -> Dict[str, Any]:
        """Mock stat."""
        entry = self._store.get(uri)
        if entry:
            return {"type": entry["type"], "uri": uri}
        raise FileNotFoundError(f"Not found: {uri}")

    async def find(self, query: str, **kwargs) -> Dict[str, Any]:
        """Mock find - searches file names and content."""
        memories = []
        query_lower = query.lower()

        for uri, entry in self._store.items():
            if entry.get("type") == "file":
                name = self._get_name_from_uri(uri)
                content = entry.get("content", "")
                if query_lower in name.lower() or query_lower in content.lower():
                    memories.append(
                        {"uri": uri, "name": name, "abstract": content[:200] if content else ""}
                    )

        return {
            "memories": memories,
            "resources": [],
            "skills": [],
        }

    async def search(self, query: str, **kwargs) -> Any:
        """Mock search."""
        return {"memories": [], "resources": [], "skills": []}

    async def tree(self, uri: str, **kwargs) -> Dict[str, Any]:
        """Mock tree."""
        return {"uri": uri, "tree": []}

    def snapshot(self) -> None:
        """Save a snapshot of the current file state."""
        self._snapshot = {}
        for uri, entry in self._store.items():
            if entry.get("type") == "file":
                self._snapshot[uri] = entry.get("content", "")

    def diff_since_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        Compute diff since last snapshot.

        Returns:
            Dict with keys 'added', 'modified', 'deleted', each mapping URIs to content.
        """
        added = {}
        modified = {}
        deleted = {}

        # Get current files
        current_files = {}
        for uri, entry in self._store.items():
            if entry.get("type") == "file":
                current_files[uri] = entry.get("content", "")

        # Check for added/modified files
        for uri, content in current_files.items():
            if uri not in self._snapshot:
                added[uri] = content
            elif content != self._snapshot[uri]:
                modified[uri] = {"old": self._snapshot[uri], "new": content}

        # Check for deleted files
        for uri in self._snapshot:
            if uri not in current_files:
                deleted[uri] = self._snapshot[uri]

        return {"added": added, "modified": modified, "deleted": deleted}


def create_test_conversation() -> List[Message]:
    """Create a test conversation focused on cards and events."""
    messages = []

    # Message 1: User starts talking about a project
    msg1 = Message(
        id="msg1",
        role="user",
        parts=[
            TextPart(
                "We're starting the memory extraction feature for the OpenViking project today. This project is an Agent-native context database."
            )
        ],
    )
    messages.append(msg1)

    # Message 2: Assistant responds
    msg2 = Message(
        id="msg2",
        role="assistant",
        parts=[
            TextPart(
                "Great! The memory extraction feature is important. What technical approach are we planning to use?"
            )
        ],
    )
    messages.append(msg2)

    # Message 3: User talks about architecture decisions
    msg3 = Message(
        id="msg3",
        role="user",
        parts=[
            TextPart(
                "We've decided to use the ExtractLoop pattern, combined with LLMs to analyze conversations and generate memory operations. "
                "There are two main memory types: cards for knowledge cards (Zettelkasten note-taking method), and events for recording important events and decisions."
            )
        ],
    )
    messages.append(msg3)

    # Message 4: Assistant asks about schemas
    msg4 = Message(
        id="msg4",
        role="assistant",
        parts=[TextPart("Got it! What's the specific structure of these two schemas?")],
    )
    messages.append(msg4)

    # Message 5: User explains schemas
    msg5 = Message(
        id="msg5",
        role="user",
        parts=[
            TextPart(
                "Cards are stored in viking://agent/{agent_space}/memories/cards, each card has name and content fields. "
                "Events are stored in viking://user/{user_space}/memories/events, each event has event_name, event_time, and content fields."
            )
        ],
    )
    messages.append(msg5)

    return messages


class TestCompressorV2:
    """Tests for SessionCompressorV2."""

    @pytest.mark.asyncio
    async def test_memory_lock_retry_logging_is_throttled(self, monkeypatch):
        warnings = []
        debug_logs = []
        monkeypatch.setattr(compressor_v2_module.logger, "warning", warnings.append)
        monkeypatch.setattr(compressor_v2_module.logger, "debug", debug_logs.append)

        last_warning_at = compressor_v2_module._log_memory_lock_retry(
            retry_count=1,
            max_retries=0,
            last_warning_at=0.0,
        )
        compressor_v2_module._log_memory_lock_retry(
            retry_count=2,
            max_retries=0,
            last_warning_at=last_warning_at,
        )

        assert len(warnings) == 1
        assert "attempt=1" in warnings[0]
        assert debug_logs == []

    @pytest.mark.asyncio
    async def test_extract_long_term_memories_includes_latest_archive_overview(self):
        """Latest archive overview should be prepended to the v2 conversation context."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("Current task")]

        class DummyOrchestrator:
            registry = object()

            @property
            def context_provider(self):
                # 返回一个 mock provider
                class DummyProvider:
                    def get_memory_schemas(self, ctx):
                        return []

                return DummyProvider()

            async def run(self):
                # 捕获最终的消息列表
                return (
                    SimpleNamespace(
                        write_uris=[],
                        edit_uris=[],
                        delete_uris=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, registry=None):
                return SimpleNamespace(
                    written_uris=[],
                    edited_uris=[],
                    deleted_uris=[],
                    errors=[],
                )

        compressor._get_or_create_react = lambda ctx=None: DummyOrchestrator()
        compressor._get_or_create_updater = lambda transaction_handle=None: DummyUpdater()

        result = await compressor.extract_long_term_memories(
            messages=messages,
            user=user,
            session_id="test-session-v2",
            ctx=ctx,
            latest_archive_overview="LATEST OVERVIEW",
        )

        assert result == []
        # Note: latest_archive_overview 功能已移除，测试需要更新

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_extract_long_term_memories(self):
        """
        Test SessionCompressorV2.extract_long_term_memories().

        Uses:
        - MockVikingFS
        - REAL VLM (from config)
        """
        # Initialize config
        initialize_openviking_config()
        config = get_openviking_config()
        logger.info(f"Using config with memory.version = {config.memory.version}")

        # Get real VLM instance
        vlm = config.vlm.get_vlm_instance()
        logger.info(f"Using VLM: {vlm}")

        # Create user and context
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)

        # Create mock VikingFS
        viking_fs = MockVikingFS()

        # Note: SessionCompressorV2 doesn't actually use vikingdb parameter
        vikingdb = None

        # Create test conversation
        messages = create_test_conversation()

        # Format conversation for display
        conversation_str = "\n".join([f"[{msg.role}]: {msg.content}" for msg in messages])

        print("=" * 80)
        print("SessionCompressorV2 TEST")
        print("=" * 80)
        print(f"\nConversation ({len(messages)} messages):")
        print("-" * 80)
        print(conversation_str[:1000] + "..." if len(conversation_str) > 1000 else conversation_str)
        print("-" * 80)

        # Create SessionCompressorV2
        compressor = SessionCompressorV2(vikingdb=vikingdb)

        # Take snapshot before running
        viking_fs.snapshot()

        # Patch get_viking_fs() to return our mock
        # Need to patch it in all the places it's used
        with patch("openviking.session.memory.extract_loop.get_viking_fs", return_value=viking_fs):
            with patch(
                "openviking.session.memory.memory_updater.get_viking_fs", return_value=viking_fs
            ):
                with patch(
                    "openviking.session.compressor_v2.get_viking_fs", return_value=viking_fs
                ):
                    # Actually call extract_long_term_memories()
                    logger.info("Calling SessionCompressorV2.extract_long_term_memories()...")
                    memories = await compressor.extract_long_term_memories(
                        messages=messages,
                        user=user,
                        session_id="test-session-v2",
                        ctx=ctx,
                        strict_extract_errors=True,
                    )

        # Verify results
        print("\n" + "=" * 80)
        print("TEST RESULTS")
        print("=" * 80)
        print(f"Returned memories list length: {len(memories)}")
        print("Note: v2 returns empty list because it writes directly to storage")
        print("=" * 80)

        # Check what changed
        diff = viking_fs.diff_since_snapshot()
        print("\nChanges detected:")
        print(f"  Added: {len(diff['added'])} files")
        print(f"  Modified: {len(diff['modified'])} files")
        print(f"  Deleted: {len(diff['deleted'])} files")

        # The list can be empty - v2 writes directly to storage
        # The important thing is that it didn't throw an exception
        assert memories is not None
        assert isinstance(memories, list)

        logger.info("Test completed successfully!")

    @pytest.mark.asyncio
    async def test_extract_long_term_memories_logs_agfs_fallback_at_debug(self):
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]

        dummy_registry = SimpleNamespace(initialize_memory_files=AsyncMock())
        dummy_orchestrator = SimpleNamespace(
            context_provider=SimpleNamespace(get_memory_schemas=lambda _ctx: []),
            _transaction_handle=None,
            run=AsyncMock(return_value=(None, [])),
        )

        with (
            patch("openviking.storage.viking_fs.get_viking_fs", return_value=None),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=None),
            patch(
                "openviking.session.memory.memory_type_registry.create_default_registry",
                return_value=dummy_registry,
            ),
            patch.object(compressor, "_get_or_create_react", return_value=dummy_orchestrator),
            patch("openviking.session.compressor_v2.logger.warning") as warning_mock,
            patch("openviking.session.compressor_v2.logger.debug") as debug_mock,
        ):
            initialize_openviking_config()
            config = get_openviking_config()
            config.memory.long_term_apply_lock_mode = "tree"
            result = await compressor.extract_long_term_memories(
                messages=messages,
                ctx=ctx,
                strict_extract_errors=False,
            )

        assert result == []
        warning_mock.assert_not_called()
        debug_mock.assert_any_call("AGFS unavailable, running memory extraction without locks")

    @pytest.mark.asyncio
    async def test_v2_lock_acquire_waits_without_retry_loop(self):
        """v2 memory extraction should delegate waiting to lock manager without local retries."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]

        class FixedSchema:
            directory = "viking://user/{{ user_space }}/memories"
            filename_template = "profile.md"

            def filename_has_variables(self):
                return False

        class VariableSchema:
            directory = "viking://user/{{ user_space }}/memories/events"
            filename_template = "{{ event_name }}.md"

            def filename_has_variables(self):
                return True

        class DummyProvider:
            def get_memory_schemas(self, _ctx):
                return [FixedSchema(), VariableSchema()]

            def _get_registry(self):
                return object()

        class DummyOrchestrator:
            context_provider = DummyProvider()

            async def run(self):
                return (
                    SimpleNamespace(
                        write_uris=[],
                        edit_uris=[],
                        delete_uris=[],
                    ),
                    [],
                )

        lock_manager = SimpleNamespace(
            create_handle=lambda: object(),
            acquire_exact_tree_batch=AsyncMock(return_value=False),
            release=AsyncMock(),
        )

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=MockVikingFS()),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch(
                "openviking.session.memory.memory_type_registry.create_default_registry",
                return_value=SimpleNamespace(initialize_memory_files=AsyncMock()),
            ),
            patch.object(compressor, "_get_or_create_react", return_value=DummyOrchestrator()),
        ):
            initialize_openviking_config()
            config = get_openviking_config()
            config.memory.long_term_apply_lock_mode = "tree"
            config.memory.v2_lock_max_retries = 2
            config.memory.v2_lock_retry_interval_seconds = 0.0
            result = await compressor.extract_long_term_memories(
                messages=messages,
                ctx=ctx,
                strict_extract_errors=False,
            )

        assert result == []
        assert lock_manager.acquire_exact_tree_batch.await_count == 2
        _, kwargs = lock_manager.acquire_exact_tree_batch.await_args
        assert kwargs["exact_paths"] == ["/local/default/user/default/memories/profile.md"]
        assert kwargs["tree_paths"] == ["/local/default/user/default/memories/events"]

    @pytest.mark.asyncio
    async def test_extract_phase_runs_post_apply_before_lock_release(self):
        """Agent experience source metadata should be updated inside the schema lock."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        events: List[str] = []

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

        class DummyProvider:
            def get_memory_schemas(self, _ctx):
                return [
                    SimpleNamespace(
                        memory_type="experiences",
                        directory="viking://agent/default/memories/experiences",
                        filename_template="{{experience_name}}.md",
                    )
                ]

            def _get_registry(self):
                return object()

        class DummyExtractLoop:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="experiences",
                                uris=["viking://agent/default/memories/experiences/debug.md"],
                                memory_fields={"experience_name": "debug"},
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.written_uris = ["viking://agent/default/memories/experiences/debug.md"]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                role_id_memory_isolation_enabled=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
            ),
        )
        handle = SimpleNamespace(id="handle-1", locks=[])

        async def acquire_exact_tree_batch(*args, **kwargs):
            events.append("acquire")
            return True

        async def release(_handle):
            events.append("release")

        lock_manager = SimpleNamespace(
            create_handle=lambda: handle,
            acquire_exact_tree_batch=AsyncMock(side_effect=acquire_exact_tree_batch),
            release=AsyncMock(side_effect=release),
        )

        async def post_apply(result, inheritance_map, lock_handle, source_attribution_map):
            assert result.written_uris == ["viking://agent/default/memories/experiences/debug.md"]
            assert inheritance_map == {}
            assert lock_handle is handle
            assert source_attribution_map == {}
            events.append("post_apply")

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor._run_extract_phase(
                    provider=DummyProvider(),
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                    phase_label="experience(test)",
                    post_apply=post_apply,
                )

        assert result[0] == ["viking://agent/default/memories/experiences/debug.md"]
        assert events == ["acquire", "apply", "post_apply", "release"]
        summary = telemetry.finish().summary
        phase = summary["memory"]["agent"]["phase"]["experience_single"]
        assert phase["count"] == 1
        assert phase.get("total_ms", 0) >= 0
        assert phase.get("lock_wait_ms", 0) >= 0
        assert phase.get("memory_apply_ms", 0) >= 0
        assert phase.get("post_apply_ms", 0) >= 0
        assert phase["schema_tree_lock_path_count"] == 1
        assert phase.get("schema_exact_lock_path_count", 0) == 0

    @pytest.mark.asyncio
    async def test_experience_operation_exact_apply_locks_after_llm(self):
        """Experimental exact-apply mode should not hold the schema tree lock during LLM."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        events: List[str] = []
        exp_uri = "viking://agent/default/memories/experiences/debug.md"

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

            async def read_file(self, uri: str, ctx=None):
                return ""

        class DummyProvider:
            prefetched_uris = [
                exp_uri,
                "viking://agent/default/memories/experiences/other.md",
            ]
            read_file_versions = {}

            def get_memory_schemas(self, _ctx):
                return []

            def _get_registry(self):
                return object()

        class DummyExtractLoop:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                events.append("llm")
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="experiences",
                                uris=[exp_uri],
                                memory_fields={"experience_name": "debug"},
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.written_uris = [exp_uri]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
                agent_experience_apply_lock_mode="operation_exact",
            ),
        )
        handle = SimpleNamespace(id="handle-1", locks=[])

        async def acquire_exact_path_batch(_handle, paths, **kwargs):
            events.append("acquire_exact")
            assert paths == [
                exp_uri,
                "viking://agent/default/memories/experiences/.overview.md",
            ]
            return True

        async def post_apply(result, inheritance_map, lock_handle, source_attribution_map):
            assert lock_handle is handle
            events.append("post_apply")

        lock_manager = SimpleNamespace(
            create_handle=lambda: handle,
            acquire_exact_tree_batch=AsyncMock(),
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            release=AsyncMock(side_effect=lambda _handle: events.append("release")),
        )

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor._run_extract_phase(
                    provider=DummyProvider(),
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                    phase_label="experience(test)",
                    post_apply=post_apply,
                )

        assert result[0] == [exp_uri]
        assert events == ["llm", "acquire_exact", "apply", "post_apply", "release"]
        lock_manager.acquire_exact_tree_batch.assert_not_called()
        phase_summary = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
        assert phase_summary["candidate_uri_count"] == 2
        assert phase_summary["operation_target_uri_count"] == 2
        assert phase_summary["candidate_target_overlap_count"] == 1
        assert phase_summary["operation_exact_lock_path_count"] == 2

    @pytest.mark.asyncio
    async def test_trajectory_operation_exact_apply_locks_after_llm(self):
        """Trajectory exact-apply mode should not hold the schema tree lock during LLM."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        events: List[str] = []
        traj_uri = "viking://agent/default/memories/trajectories/debug_20260524010101.md"

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

            async def read_file(self, uri: str, ctx=None):
                return ""

        class DummyProvider:
            prefetched_uris = []
            read_file_versions = {}

            def get_memory_schemas(self, _ctx):
                return []

            def _get_registry(self):
                return object()

        class DummyExtractLoop:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                events.append("llm")
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="trajectories",
                                uris=[traj_uri],
                                memory_fields={"trajectory_name": "debug"},
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.written_uris = [traj_uri]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
                agent_experience_apply_lock_mode="tree",
                agent_trajectory_apply_lock_mode="operation_exact",
            ),
        )
        handle = SimpleNamespace(id="handle-trajectory", locks=[])

        async def acquire_exact_path_batch(_handle, paths, **kwargs):
            events.append("acquire_exact")
            assert paths == [
                traj_uri,
                "viking://agent/default/memories/trajectories/.overview.md",
            ]
            return True

        lock_manager = SimpleNamespace(
            create_handle=lambda: handle,
            acquire_exact_tree_batch=AsyncMock(),
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            release=AsyncMock(side_effect=lambda _handle: events.append("release")),
        )

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor._run_extract_phase(
                    provider=DummyProvider(),
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                    phase_label="trajectory",
                )

        assert result[0] == [traj_uri]
        assert events == ["llm", "acquire_exact", "apply", "release"]
        lock_manager.acquire_exact_tree_batch.assert_not_called()
        phase_summary = telemetry.finish().summary["memory"]["agent"]["phase"]["trajectory"]
        assert phase_summary["operation_exact_lock_path_count"] == 2

    @pytest.mark.asyncio
    async def test_long_term_operation_exact_apply_locks_tools_after_llm(self):
        """Long-term operation-exact mode should avoid schema tree locks for tools memory."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        events: List[str] = []
        tool_uri = "viking://agent/default/memories/tools/search_docs.md"

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

            async def read_file(self, uri: str, ctx=None):
                return ""

        class DummyExtractLoop:
            def __init__(self, **kwargs):
                self.provider = kwargs["context_provider"]

            async def run(self):
                events.append("llm")
                assert self.provider._track_read_file_versions is True
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="tools",
                                uris=[tool_uri],
                                memory_fields={"tool_name": "search_docs"},
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.written_uris = [tool_uri]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
                long_term_apply_lock_mode="operation_exact",
            ),
        )
        handle = SimpleNamespace(id="handle-long-term", locks=[])

        async def acquire_exact_path_batch(_handle, paths, **kwargs):
            events.append("acquire_exact")
            assert paths == [
                tool_uri,
                "viking://agent/default/memories/tools/.overview.md",
            ]
            return True

        lock_manager = SimpleNamespace(
            create_handle=lambda: handle,
            acquire_exact_tree_batch=AsyncMock(),
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            release=AsyncMock(side_effect=lambda _handle: events.append("release")),
        )

        with (
            patch("openviking.storage.viking_fs.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch(
                "openviking.session.memory.memory_type_registry.create_default_registry",
                return_value=SimpleNamespace(initialize_memory_files=AsyncMock()),
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor.extract_long_term_memories(
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                )

        assert [context.uri for context in result] == [tool_uri]
        assert events == ["llm", "acquire_exact", "apply", "release"]
        lock_manager.acquire_exact_tree_batch.assert_not_called()
        phase_summary = telemetry.finish().summary["memory"]["agent"]["phase"]["other"]
        assert phase_summary["operation_exact_lock_path_count"] == 2

    @pytest.mark.asyncio
    async def test_experience_operation_exact_apply_detects_stale_prefetch(self):
        """Exact-apply mode should keep retrying with refreshed reads after stale reads."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        exp_uri = "viking://agent/default/memories/experiences/debug.md"
        events: List[str] = []

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

            async def read_file(self, uri: str, ctx=None):
                return "new-content"

        class DummyProvider:
            def __init__(self):
                self._read_file_versions = {exp_uri: content_digest("old-content")}
                self._read_file_contents = {}

            @property
            def read_file_versions(self):
                return self._read_file_versions

            def get_memory_schemas(self, _ctx):
                return []

            def _get_registry(self):
                return object()

        class DummyExtractLoop:
            run_count = 0

            def __init__(self, **kwargs):
                self.provider = kwargs["context_provider"]

            async def run(self):
                DummyExtractLoop.run_count += 1
                events.append("llm")
                if DummyExtractLoop.run_count < 3:
                    self.provider._read_file_versions[exp_uri] = content_digest(
                        f"old-content-{DummyExtractLoop.run_count}"
                    )
                else:
                    self.provider._read_file_versions[exp_uri] = content_digest("new-content")
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="experiences",
                                uris=[exp_uri],
                                memory_fields={"experience_name": "debug"},
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.written_uris = [exp_uri]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
                agent_experience_apply_lock_mode="operation_exact",
            ),
        )
        handles = [
            SimpleNamespace(id="handle-1", locks=[]),
            SimpleNamespace(id="handle-2", locks=[]),
            SimpleNamespace(id="handle-3", locks=[]),
        ]

        async def acquire_exact_path_batch(_handle, _paths, **kwargs):
            events.append("acquire_exact")
            return True

        async def acquire_exact_tree_batch(_handle, **kwargs):
            events.append("acquire_tree")
            return True

        async def release(_handle):
            events.append(f"release:{_handle.id}")

        lock_manager = SimpleNamespace(
            create_handle=Mock(side_effect=handles),
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            acquire_exact_tree_batch=AsyncMock(side_effect=acquire_exact_tree_batch),
            release=AsyncMock(side_effect=release),
        )

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor._run_extract_phase(
                    provider=DummyProvider(),
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                    phase_label="experience(test)",
                )

        assert result[0] == [exp_uri]
        assert events == [
            "llm",
            "acquire_exact",
            "release:handle-1",
            "llm",
            "acquire_exact",
            "release:handle-2",
            "llm",
            "acquire_exact",
            "apply",
            "release:handle-3",
        ]
        lock_manager.acquire_exact_tree_batch.assert_not_called()
        phase_summary = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
        assert phase_summary["operation_exact_conflicts"] == 2
        assert phase_summary["operation_exact_retries"] == 2
        assert phase_summary["operation_exact_stale_read_uri_count"] == 2
        assert phase_summary["operation_exact_retry_attempt"] == 2
        assert phase_summary["operation_exact_conflict_sensitive_buckets"] == {"experiences": 3}
        assert phase_summary["operation_exact_conflict_sensitive_reasons"] == {"unknown_schema": 3}
        assert phase_summary["operation_exact_conflict_buckets"] == {"experiences": 2}
        assert phase_summary["operation_exact_conflict_reasons"] == {"unknown_schema": 2}
        assert phase_summary["operation_exact_retry_buckets"] == {"experiences": 2}
        assert phase_summary["operation_exact_retry_reasons"] == {"unknown_schema": 2}
        assert phase_summary["operation_exact_stale_base_states"] == {"present": 2}
        assert phase_summary["operation_exact_stale_current_states"] == {"present": 2}

    @pytest.mark.asyncio
    async def test_long_term_operation_exact_allows_merge_safe_stale_prefetch(self):
        """Patch/sum long-term operations should queue at apply without rerunning the LLM."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        tool_uri = "viking://agent/default/memories/tools/search.md"
        events: List[str] = []

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

            async def read_file(self, uri: str, ctx=None):
                return "new-content"

        schema = MemoryTypeSchema(
            memory_type="tools",
            directory="viking://agent/{{ agent_space }}/memories/tools",
            filename_template="{{ tool_name }}.md",
            fields=[
                MemoryField(
                    name="tool_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="call_count",
                    field_type=FieldType.INT64,
                    merge_op=MergeOp.SUM,
                ),
                MemoryField(
                    name="when_to_use",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.PATCH,
                ),
            ],
        )
        registry = SimpleNamespace(get=lambda name: schema if name == "tools" else None)

        class DummyProvider:
            def __init__(self):
                self._read_file_versions = {tool_uri: content_digest("old-content")}
                self._read_file_contents = {}

            @property
            def read_file_versions(self):
                return self._read_file_versions

            def get_memory_schemas(self, _ctx):
                return []

            def _get_registry(self):
                return registry

        class DummyExtractLoop:
            run_count = 0

            def __init__(self, **kwargs):
                pass

            async def run(self):
                DummyExtractLoop.run_count += 1
                events.append("llm")
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="tools",
                                uris=[tool_uri],
                                memory_fields={
                                    "tool_name": "search",
                                    "call_count": 1,
                                    "when_to_use": {
                                        "blocks": [{"search": "old", "replace": "new"}]
                                    },
                                },
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.edited_uris = [tool_uri]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
                long_term_apply_lock_mode="operation_exact",
            ),
        )
        handle = SimpleNamespace(id="handle-1", locks=[])

        async def acquire_exact_path_batch(_handle, _paths, **kwargs):
            events.append("acquire_exact")
            return True

        async def release(_handle):
            events.append(f"release:{_handle.id}")

        lock_manager = SimpleNamespace(
            create_handle=Mock(return_value=handle),
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            acquire_exact_tree_batch=AsyncMock(),
            release=AsyncMock(side_effect=release),
        )

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor._run_extract_phase(
                    provider=DummyProvider(),
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                    phase_label="long_term",
                )

        assert result[1] == [tool_uri]
        assert DummyExtractLoop.run_count == 1
        assert events == ["llm", "acquire_exact", "apply", "release:handle-1"]
        lock_manager.acquire_exact_tree_batch.assert_not_called()
        phase_summary = telemetry.finish().summary["memory"]["agent"]["phase"]["other"]
        assert phase_summary.get("operation_exact_conflict_sensitive_uri_count", 0) == 0
        assert "operation_exact_conflicts" not in phase_summary
        assert "operation_exact_retries" not in phase_summary

    @pytest.mark.asyncio
    async def test_long_term_operation_exact_retries_plain_string_patch(self):
        """Plain string patches should retry with fresh reads before updating a stale target."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]
        tool_uri = "viking://agent/default/memories/tools/search.md"
        events: List[str] = []

        class FakeVikingFS:
            agfs = object()

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return uri

            async def read_file(self, uri: str, ctx=None):
                return "new-content"

        schema = MemoryTypeSchema(
            memory_type="tools",
            directory="viking://agent/{{ agent_space }}/memories/tools",
            filename_template="{{ tool_name }}.md",
            fields=[
                MemoryField(
                    name="tool_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="when_to_use",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.PATCH,
                ),
            ],
        )
        registry = SimpleNamespace(get=lambda name: schema if name == "tools" else None)

        class DummyProvider:
            def __init__(self):
                self._read_file_versions = {}
                self._read_file_contents = {}

            @property
            def read_file_versions(self):
                return self._read_file_versions

            def get_memory_schemas(self, _ctx):
                return []

            def _get_registry(self):
                return registry

        provider = DummyProvider()

        class DummyExtractLoop:
            run_count = 0

            def __init__(self, **kwargs):
                self.provider = kwargs["context_provider"]

            async def run(self):
                DummyExtractLoop.run_count += 1
                events.append(f"llm:{DummyExtractLoop.run_count}")
                self.provider._read_file_versions = {
                    tool_uri: content_digest(
                        "old-content" if DummyExtractLoop.run_count == 1 else "new-content"
                    )
                }
                return (
                    ResolvedOperations(
                        upsert_operations=[
                            ResolvedOperation(
                                memory_type="tools",
                                uris=[tool_uri],
                                memory_fields={
                                    "tool_name": "search",
                                    "when_to_use": "replace the whole description",
                                },
                            )
                        ],
                        delete_file_contents=[],
                        errors=[],
                    ),
                    [],
                )

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.edited_uris = [tool_uri]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
                v2_lock_max_retries=1,
                v2_lock_retry_interval_seconds=0.0,
                long_term_apply_lock_mode="operation_exact",
            ),
        )
        handles = [
            SimpleNamespace(id="handle-1", locks=[]),
            SimpleNamespace(id="handle-2", locks=[]),
        ]
        handle_iter = iter(handles)

        async def acquire_exact_path_batch(handle, _paths, **kwargs):
            events.append(f"acquire:{handle.id}")
            return True

        async def release(handle):
            events.append(f"release:{handle.id}")

        lock_manager = SimpleNamespace(
            create_handle=Mock(side_effect=lambda: next(handle_iter)),
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            acquire_exact_tree_batch=AsyncMock(),
            release=AsyncMock(side_effect=release),
        )

        with (
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch(
                "openviking.session.memory.memory_isolation_handler.get_openviking_config",
                return_value=config,
            ),
            patch("openviking.session.compressor_v2.ExtractLoop", DummyExtractLoop),
            patch("openviking.storage.transaction.init_lock_manager"),
            patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager),
            patch.object(compressor, "_get_or_create_updater", return_value=DummyUpdater()),
        ):
            telemetry = OperationTelemetry(operation="session.commit", enabled=True)
            with bind_telemetry(telemetry):
                result = await compressor._run_extract_phase(
                    provider=provider,
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=True,
                    phase_label="long_term",
                )

        assert result[1] == [tool_uri]
        assert DummyExtractLoop.run_count == 2
        assert events == [
            "llm:1",
            "acquire:handle-1",
            "release:handle-1",
            "llm:2",
            "acquire:handle-2",
            "apply",
            "release:handle-2",
        ]
        lock_manager.acquire_exact_tree_batch.assert_not_called()
        phase_summary = telemetry.finish().summary["memory"]["agent"]["phase"]["other"]
        assert phase_summary["operation_exact_conflicts"] == 1
        assert phase_summary["operation_exact_retries"] == 1
        assert phase_summary["operation_exact_stale_read_uri_count"] == 1
        assert phase_summary["operation_exact_conflict_buckets"] == {"tools": 1}
        assert phase_summary["operation_exact_conflict_reasons"] == {"plain_string_patch": 1}
        assert phase_summary["operation_exact_retry_buckets"] == {"tools": 1}
        assert phase_summary["operation_exact_retry_reasons"] == {"plain_string_patch": 1}
        assert phase_summary["operation_exact_stale_base_states"] == {"present": 1}
        assert phase_summary["operation_exact_stale_current_states"] == {"present": 1}

    @pytest.mark.asyncio
    async def test_append_trajectories_uses_exact_lock(self):
        """Fallback source metadata append should protect the read-modify-write."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        exp_uri = "viking://agent/default/memories/experiences/debug.md"
        events: List[str] = []

        traj_uri = "viking://agent/default/memories/trajectories/traj-1.md"

        class FakeVikingFS:
            def __init__(self):
                self.files = {
                    exp_uri: MemoryFileUtils.write(
                        MemoryFile(uri=exp_uri, content="debug login issue")
                    ),
                    traj_uri: MemoryFileUtils.write(
                        MemoryFile(uri=traj_uri, content="traj content")
                    ),
                }

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return f"/local/default/agent/default/memories/experiences/{uri.rsplit('/', 1)[-1]}"

            async def read_file(self, uri: str, ctx=None):
                events.append("read")
                return self.files.get(uri, "")

            async def write_file(self, uri: str, content: str, ctx=None):
                events.append("write")
                self.files[uri] = content

        handle = SimpleNamespace(id="handle-1", locks=[])

        async def acquire_exact_path_batch(_handle, paths):
            events.append(f"exact:{paths[0]}")
            return True

        async def release(_handle):
            events.append("release")

        lock_manager = SimpleNamespace(
            create_handle=lambda: handle,
            acquire_exact_path_batch=AsyncMock(side_effect=acquire_exact_path_batch),
            release=AsyncMock(side_effect=release),
        )
        viking_fs = FakeVikingFS()

        with patch("openviking.storage.transaction.get_lock_manager", return_value=lock_manager):
            await compressor._append_trajectories_to_experiences(
                [exp_uri],
                [traj_uri],
                ctx,
                viking_fs,
            )

        # exp: exp.links 有指向 traj 的边（exp→traj, derived_from）
        exp_mf = MemoryFileUtils.read(viking_fs.files[exp_uri], uri=exp_uri)
        assert "source_trajectories" not in exp_mf.extra_fields
        assert any(l.get("to_uri") == traj_uri for l in exp_mf.links), (
            "exp.links should point to traj"
        )
        assert exp_mf.backlinks == [], "exp should have no backlinks"

        # traj: write_stored_links 写入 traj.backlinks（同一条边的 to 端）
        traj_mf = MemoryFileUtils.read(viking_fs.files[traj_uri], uri=traj_uri)
        assert traj_mf.links == [], "traj should have no forward links"
        assert any(l.get("from_uri") == exp_uri for l in traj_mf.backlinks), (
            "traj.backlinks should reference exp"
        )

        # event order: lock → read exp → write exp → read traj → write traj → release
        assert events == [
            "exact:/local/default/agent/default/memories/experiences/debug.md",
            "read",  # exp read
            "write",  # exp write (exp.links)
            "read",  # traj read  (write_stored_links)
            "write",  # traj write (traj.backlinks)
            "release",
        ]

    @pytest.mark.asyncio
    async def test_agent_memory_default_keeps_per_trajectory_experience_phases(self):
        """Default config should preserve one experience phase per trajectory."""
        from openviking.session.memory.agent_experience_context_provider import (
            AgentExperienceContextProvider,
        )

        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = create_test_conversation()
        traj_uris = [
            "viking://agent/default/memories/trajectories/first.md",
            "viking://agent/default/memories/trajectories/second.md",
        ]
        calls: List[str] = []

        class FakeVikingFS:
            async def read_file(self, uri: str, ctx=None):
                return MemoryFileUtils.write(
                    MemoryFile(uri=uri, content=f"content for {uri}", extra_fields={})
                )

        async def fake_run_extract_phase(**kwargs):
            phase_label = kwargs["phase_label"]
            calls.append(phase_label)
            if phase_label == "trajectory":
                return traj_uris, [], [], {}, []

            assert isinstance(kwargs["provider"], AgentExperienceContextProvider)
            assert phase_label.startswith(
                "experience(viking://agent/default/memories/trajectories/"
            )
            return [f"exp-for-{len(calls)}"], [], [], {}, []

        config = SimpleNamespace(
            memory=SimpleNamespace(
                agent_memory_enabled=True,
            )
        )

        with (
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch.object(compressor, "_run_extract_phase", side_effect=fake_run_extract_phase),
        ):
            await compressor.extract_agent_memories(messages, ctx=ctx)

        assert calls == [
            "trajectory",
            f"experience({traj_uris[0]})",
            f"experience({traj_uris[1]})",
        ]

    @pytest.mark.asyncio
    async def test_agent_memory_operation_exact_runs_per_trajectory_experience_concurrently(self):
        """Operation-exact apply can overlap same-session per-trajectory experience phases."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = create_test_conversation()
        traj_uris = [
            "viking://agent/default/memories/trajectories/first.md",
            "viking://agent/default/memories/trajectories/second.md",
        ]
        phase_labels: List[str] = []
        first_started = asyncio.Event()
        second_started = asyncio.Event()

        class FakeVikingFS:
            async def read_file(self, uri: str, ctx=None):
                return MemoryFileUtils.write(
                    MemoryFile(uri=uri, content=f"content for {uri}", extra_fields={})
                )

        async def fake_run_extract_phase(**kwargs):
            phase_label = kwargs["phase_label"]
            phase_labels.append(phase_label)
            if phase_label == "trajectory":
                return traj_uris, [], [], {}, []

            if phase_label == f"experience({traj_uris[0]})":
                first_started.set()
                await asyncio.wait_for(second_started.wait(), timeout=1)
            elif phase_label == f"experience({traj_uris[1]})":
                second_started.set()
                await asyncio.wait_for(first_started.wait(), timeout=1)
            else:
                pytest.fail(f"unexpected phase label: {phase_label}")
            return [f"exp-for-{phase_label}"], [], [], {}, []

        config = SimpleNamespace(
            memory=SimpleNamespace(
                agent_memory_enabled=True,
                agent_experience_apply_lock_mode="operation_exact",
                agent_experience_per_trajectory_max_concurrency=2,
            )
        )

        with (
            patch("openviking.session.compressor_v2.get_openviking_config", return_value=config),
            patch("openviking.session.compressor_v2.get_viking_fs", return_value=FakeVikingFS()),
            patch.object(compressor, "_run_extract_phase", side_effect=fake_run_extract_phase),
        ):
            await asyncio.wait_for(compressor.extract_agent_memories(messages, ctx=ctx), timeout=2)

        assert phase_labels[0] == "trajectory"
        assert set(phase_labels[1:]) == {
            f"experience({traj_uris[0]})",
            f"experience({traj_uris[1]})",
        }


class TestExtractLoopPatchRepair:
    """Tests for ExtractLoop patch validation and repair retry."""

    @pytest.mark.asyncio
    async def test_invalid_patch_search_triggers_one_repair_retry(self):
        schema = MemoryTypeSchema(
            memory_type="profile",
            description="User profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    description="Profile content",
                    merge_op=MergeOp.PATCH,
                )
            ],
        )
        target_uri = "viking://user/default/memories/profile.md"
        other_uri = "viking://user/default/memories/other.md"
        target_file = MemoryFile(uri=target_uri, content="# Tim\n- Likes reading")
        other_file = MemoryFile(uri=other_uri, content="# Other\n- Has been reading as usual")

        class DummyRegistry:
            def get(self, memory_type):
                assert memory_type == "profile"
                return schema

        class DummyProvider:
            read_file_contents = {
                target_uri: target_file,
                other_uri: other_file,
            }

            def __init__(self):
                self.extract_context = ExtractContext([])

            def get_memory_schemas(self, _ctx):
                return [schema]

            def get_output_language(self):
                return "English"

            def get_tools(self):
                return []

            def instruction(self):
                return "Extract memories."

            async def prefetch(self):
                return []

            def get_extract_context(self):
                return self.extract_context

            def _get_registry(self):
                return DummyRegistry()

        class DummyIsolationHandler:
            def get_read_scope(self):
                return RoleScope(user_ids=["default"], agent_ids=["default"])

            def fill_role_ids(self, item_dict, role_scope):
                item_dict.setdefault("user_id", "default")
                item_dict.setdefault("agent_id", "default")

            def calculate_memory_uris(self, memory_type_schema, operation, extract_context):
                return [target_uri]

        class DummyVLM:
            model = "dummy"

            def __init__(self):
                self.responses = [
                    '{"profile":[{"page_id":1,"content":{"blocks":[{"search":"- Has been reading as usual","replace":"- Has been reading as usual (as of 2023-11-11)"}]} }],"delete_uris":[]}',
                    '{"profile":[{"page_id":1,"content":{"blocks":[{"search":"- Likes reading","replace":"- Likes reading\n- Has been reading as usual (as of 2023-11-11)"}]} }],"delete_uris":[]}',
                ]
                self.messages = []

            async def get_completion_async(self, messages, tools=None, tool_choice=None):
                self.messages.append(list(messages))
                return self.responses.pop(0)

        vlm = DummyVLM()
        loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MockVikingFS(),
            max_iterations=1,
            context_provider=DummyProvider(),
            isolation_handler=DummyIsolationHandler(),
        )

        operations, _tools_used = await loop.run()

        assert len(vlm.messages) == 2
        second_call_content = "\n".join(message["content"] for message in vlm.messages[1])
        assert "SEARCH/REPLACE patch could not be applied" in second_call_content
        assert "Regenerate the complete operations JSON" in second_call_content
        assert target_uri in second_call_content
        assert other_uri in second_call_content
        assert (
            operations.upsert_operations[0].memory_fields["content"].blocks[0].search
            == "- Likes reading"
        )

    @pytest.mark.asyncio
    async def test_invalid_patch_search_repairs_only_once(self):
        schema = MemoryTypeSchema(
            memory_type="profile",
            description="User profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    description="Profile content",
                    merge_op=MergeOp.PATCH,
                )
            ],
        )
        target_uri = "viking://user/default/memories/profile.md"
        target_file = MemoryFile(uri=target_uri, content="# Tim\n- Likes reading")

        class DummyRegistry:
            def get(self, memory_type):
                assert memory_type == "profile"
                return schema

        class DummyProvider:
            read_file_contents = {target_uri: target_file}

            def __init__(self):
                self.extract_context = ExtractContext([])

            def get_memory_schemas(self, _ctx):
                return [schema]

            def get_output_language(self):
                return "English"

            def get_tools(self):
                return []

            def instruction(self):
                return "Extract memories."

            async def prefetch(self):
                return []

            def get_extract_context(self):
                return self.extract_context

            def _get_registry(self):
                return DummyRegistry()

        class DummyIsolationHandler:
            def get_read_scope(self):
                return RoleScope(user_ids=["default"], agent_ids=["default"])

            def fill_role_ids(self, item_dict, role_scope):
                item_dict.setdefault("user_id", "default")
                item_dict.setdefault("agent_id", "default")

            def calculate_memory_uris(self, memory_type_schema, operation, extract_context):
                return [target_uri]

        class DummyVLM:
            model = "dummy"

            def __init__(self):
                self.responses = [
                    '{"profile":[{"page_id":1,"content":{"blocks":[{"search":"- Missing one","replace":"- Fixed one"}]} }],"delete_uris":[]}',
                    '{"profile":[{"page_id":1,"content":{"blocks":[{"search":"- Missing two","replace":"- Fixed two"}]} }],"delete_uris":[]}',
                ]
                self.messages = []

            async def get_completion_async(self, messages, tools=None, tool_choice=None):
                self.messages.append(list(messages))
                return self.responses.pop(0)

        vlm = DummyVLM()
        loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MockVikingFS(),
            max_iterations=1,
            context_provider=DummyProvider(),
            isolation_handler=DummyIsolationHandler(),
        )

        operations, _tools_used = await loop.run()

        assert len(vlm.messages) == 2
        all_messages = "\n".join(
            message["content"] for call_messages in vlm.messages for message in call_messages
        )
        assert all_messages.count("SEARCH/REPLACE patch could not be applied") == 1
        assert (
            operations.upsert_operations[0].memory_fields["content"].blocks[0].search
            == "- Missing two"
        )

    @pytest.mark.asyncio
    async def test_fuzzy_patch_success_does_not_trigger_repair(self):
        schema = MemoryTypeSchema(
            memory_type="profile",
            description="User profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    description="Profile content",
                    merge_op=MergeOp.PATCH,
                )
            ],
        )
        target_uri = "viking://user/default/memories/profile.md"
        target_file = MemoryFile(uri=target_uri, content="# Tim\n- Likes reading every night")

        class DummyRegistry:
            def get(self, memory_type):
                assert memory_type == "profile"
                return schema

        class DummyProvider:
            read_file_contents = {target_uri: target_file}

            def __init__(self):
                self.extract_context = ExtractContext([])

            def get_memory_schemas(self, _ctx):
                return [schema]

            def get_output_language(self):
                return "English"

            def get_tools(self):
                return []

            def instruction(self):
                return "Extract memories."

            async def prefetch(self):
                return []

            def get_extract_context(self):
                return self.extract_context

            def _get_registry(self):
                return DummyRegistry()

        class DummyIsolationHandler:
            def get_read_scope(self):
                return RoleScope(user_ids=["default"], agent_ids=["default"])

            def fill_role_ids(self, item_dict, role_scope):
                item_dict.setdefault("user_id", "default")
                item_dict.setdefault("agent_id", "default")

            def calculate_memory_uris(self, memory_type_schema, operation, extract_context):
                return [target_uri]

        class DummyVLM:
            model = "dummy"

            def __init__(self):
                self.responses = [
                    '{"profile":[{"page_id":1,"content":{"blocks":[{"search":"- Likes reading","replace":"- Likes reading every night (as of 2023-11-11)"}]} }],"delete_uris":[]}',
                ]
                self.messages = []

            async def get_completion_async(self, messages, tools=None, tool_choice=None):
                self.messages.append(list(messages))
                return self.responses.pop(0)

        vlm = DummyVLM()
        loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MockVikingFS(),
            max_iterations=1,
            context_provider=DummyProvider(),
            isolation_handler=DummyIsolationHandler(),
        )

        operations, _tools_used = await loop.run()

        assert len(vlm.messages) == 1
        assert (
            operations.upsert_operations[0].memory_fields["content"].blocks[0].search
            == "- Likes reading"
        )
