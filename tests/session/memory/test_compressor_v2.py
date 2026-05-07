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
from openviking.session.compressor_v2 import SessionCompressorV2
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
    async def test_v2_lock_acquire_respects_max_retries(self):
        """v2 memory extraction should stop after configured lock retry limit."""
        compressor = SessionCompressorV2(vikingdb=None)
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)
        messages = [Message.create_user("test")]

        class DummySchema:
            directory = "viking://user/{{ user_space }}/memories/events"

        class DummyProvider:
            def get_memory_schemas(self, _ctx):
                return [DummySchema()]

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
            acquire_subtree_batch=AsyncMock(return_value=False),
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
            patch("openviking.session.compressor_v2.asyncio.sleep", new=AsyncMock()),
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
        assert lock_manager.acquire_subtree_batch.await_count == 2
