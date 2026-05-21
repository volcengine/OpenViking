# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test for SessionCompressorV2.

Uses MockVikingFS and real VLM (from config).
"""

import logging
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session import compressor_v2 as compressor_v2_module
from openviking.session.compressor_v2 import SessionCompressorV2
from openviking.session.memory.dataclass import MemoryField, MemoryFile, MemoryTypeSchema
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import RoleScope
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config, initialize_openviking_config

# Let openviking logger propagate to pytest
for logger_name in ["openviking", "openviking.session.memory"]:
    logger = logging.getLogger(logger_name)
    logger.propagate = True
    logger.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


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
                return []

            def _get_registry(self):
                return object()

        class DummyExtractLoop:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                return SimpleNamespace(upsert_operations=[], delete_file_contents=[]), []

        class DummyUpdater:
            async def apply_operations(self, operations, ctx, **kwargs):
                events.append("apply")
                result = MemoryUpdateResult()
                result.written_uris = ["viking://agent/default/memories/experiences/debug.md"]
                return result

        config = SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: object()),
            memory=SimpleNamespace(
                enable_role_id_memory_isolate=False,
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

        async def post_apply(result, inheritance_map, lock_handle):
            assert result.written_uris == ["viking://agent/default/memories/experiences/debug.md"]
            assert inheritance_map == {}
            assert lock_handle is handle
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

    @pytest.mark.asyncio
    async def test_append_trajectories_uses_exact_lock(self):
        """Fallback source metadata append should protect the read-modify-write."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        exp_uri = "viking://agent/default/memories/experiences/debug.md"
        events: List[str] = []

        class FakeVikingFS:
            def __init__(self):
                self.content = MemoryFileUtils.write(
                    MemoryFile(
                        uri="viking://agent/default/memories/experiences/debug.md",
                        content="debug login issue",
                        extra_fields={"source_trajectories": ["traj-0"]},
                    )
                )

            def _uri_to_path(self, uri: str, ctx=None) -> str:
                return f"/local/default/agent/default/memories/experiences/{uri.rsplit('/', 1)[-1]}"

            async def read_file(self, uri: str, ctx=None):
                events.append("read")
                return self.content

            async def write_file(self, uri: str, content: str, ctx=None):
                events.append("write")
                self.content = content

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
                ["traj-1"],
                ctx,
                viking_fs,
            )

        mf = MemoryFileUtils.read(viking_fs.content, uri=exp_uri)
        assert mf.extra_fields["source_trajectories"] == ["traj-0", "traj-1"]
        assert events == [
            "exact:/local/default/agent/default/memories/experiences/debug.md",
            "read",
            "write",
            "release",
        ]


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
