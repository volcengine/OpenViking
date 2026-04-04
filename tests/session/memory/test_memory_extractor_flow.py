# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test memory extraction flow with memory module components.

This test simulates the complete memory extraction workflow:
1. Setup conversation messages
2. Pre-fetch memory directory structure
3. Call ReAct orchestrator to analyze and determine memory changes
4. Generate memory operations
5. Apply operations via MemoryUpdater
"""

import logging
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import pytest

# 让 openviking logger 的日志 propagate 到 pytest
for logger_name in ["openviking", "openviking.session.memory"]:
    logger = logging.getLogger(logger_name)
    logger.propagate = True
    logger.setLevel(logging.DEBUG)

# Module logger for this test
logger = logging.getLogger(__name__)

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory import (
    ExtractLoop,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config, initialize_openviking_config


class MockVikingFS:
    """Mock VikingFS for testing with unified memory storage."""

    def __init__(self):
        # Unified storage: key is URI, value is dict with type and content/children
        self._store: Dict[str, Dict[str, Any]] = {}
        self._snapshot: Dict[str, str] = {}

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
            parent["children"] = [c for c in parent["children"] if c.get("name") != name]
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


def print_diff(diff: Dict[str, Dict[str, str]]) -> None:
    """
    Print diff in a readable format using diff-match-patch with colors.
    Uses inline color codes to show character-level changes on the same line.
    """
    # ANSI color codes - 9m=删除线，31m=红色字体，32m=绿色
    STYLE_DELETE = "\033[9m\033[31m"
    STYLE_INSERT = "\033[32m"
    STYLE_RESET = "\033[0m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"

    try:
        from diff_match_patch import diff_match_patch

        has_dmp = True
    except ImportError:
        has_dmp = False

    print("\n" + "=" * 80)
    print(f"{YELLOW}MEMORY CHANGES DIFF (Character-level){STYLE_RESET}")
    print("=" * 80)

    # Added files
    if diff["added"]:
        print(f"\n{GREEN}[ADDED] {len(diff['added'])} file(s):{STYLE_RESET}")
        for uri, content in diff["added"].items():
            print(f"\n  {uri}")
            print("  " + "-" * 76)
            for line in content.split("\n"):
                print(f"{GREEN}  + {line}{STYLE_RESET}")

    # Modified files
    if diff["modified"]:
        print(f"\n[MODIFIED] {len(diff['modified'])} file(s):")
        for uri, changes in diff["modified"].items():
            print(f"\n  {uri}")
            print("  " + "-" * 76)

            old_text = changes["old"] or ""
            new_text = changes["new"] or ""

            if has_dmp and old_text and new_text:
                try:
                    dmp = diff_match_patch()
                    # Compute character-level diff and clean up
                    diffs = dmp.diff_main(old_text, new_text)
                    dmp.diff_cleanupSemantic(diffs)  # 优化diff结果，减少冗余

                    # Format output with inline colors - character-level on same line
                    _print_inline_diff(diffs, STYLE_DELETE, STYLE_INSERT, STYLE_RESET)
                except Exception as e:
                    # Fallback to simple line-by-line comparison
                    logger.exception("diff_match_patch fail", e)
            else:
                logger.exception("has_dmp= False")

    # Deleted files
    if diff["deleted"]:
        print(f"\n{RED}[DELETED] {len(diff['deleted'])} file(s):{STYLE_RESET}")
        for uri, content in diff["deleted"].items():
            print(f"\n  {uri}")
            print("  " + "-" * 76)
            for line in content.split("\n"):
                print(f"{RED}  - {line}{STYLE_RESET}")

    if not any(diff.values()):
        print("\n  No changes detected.")

    print("\n" + "=" * 80 + "\n")


def _print_inline_diff(
    diffs: List[Tuple[int, str]], style_delete: str, style_insert: str, style_reset: str
) -> None:
    """
    Print character-level diff with inline colors.

    Shows deletions in red strikethrough and insertions in green,
    all in the same line flow for easy reading.
    """
    output = []

    for op, text in diffs:
        if op == 0:  # 文本无差异：正常显示
            output.append(f"{text}")
        elif op == -1:  # 文本删除：红色删除线
            output.append("\n".join([f"{style_delete}{t}{style_reset}" for t in text.split("\n")]))
        elif op == 1:  # 文本新增：绿色
            output.append("\n".join([f"{style_insert}{t}{style_reset}" for t in text.split("\n")]))

    # 合并并打印最终结果，添加行号
    formatted_text = "".join(output)
    for idx, line in enumerate(formatted_text.split("\n")):
        print(f"  {idx + 1}: {line}")


def create_test_conversation() -> List[Message]:
    """Create a test conversation focused on cards and events."""
    user = UserIdentifier.the_default_user()
    ctx = RequestContext(user=user, role=Role.ROOT)

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
                "Events are stored in viking://user/{user_space}/memories/events, each event has event_name, event_time, and content fields. "
                "Just now, we also decided to add diff-match-patch to print memory modification differences."
            )
        ],
    )
    messages.append(msg5)

    return messages


def create_existing_memories_content() -> Dict[str, str]:
    """Create existing memory content for update test with cards and events."""
    return {
        "viking://agent/default/memories/cards/openviking_project.md": """# OpenViking Project

## Overview
OpenViking is an Agent-native context database.

## Technical Approach
- Uses ExtractLoop pattern
- Combines LLM to analyze conversations and generate memory operations


<!-- MEMORY_FIELDS
{
  "name": "openviking_project"
}
-->""",
        "viking://agent/default/memories/cards/extract_loop.md": """# ExtractLoop Pattern

## Overview
ExtractLoop is an orchestrator pattern for memory extraction.

## Features
- Analyze conversation content
- Generate memory operations


<!-- MEMORY_FIELDS
{
  "name": "extract_loop"
}
-->""",
        "viking://user/default/memories/events/2026-03-20_Started_memory_extraction_feature_development.md": """# Event: Started memory extraction feature development

## Event Name
Started memory extraction feature development

## Event Time
2026-03-20

## Content
Today we started working on the memory extraction feature for the OpenViking project. Decided to use the ExtractLoop pattern.


<!-- MEMORY_FIELDS
{
  "event_name": "Started_memory_extraction_feature_development",
  "event_time": "2026-03-20"
}
-->""",
    }


def create_update_conversation() -> List[Message]:
    """Create a conversation for updating existing cards and events."""
    user = UserIdentifier.the_default_user()
    ctx = RequestContext(user=user, role=Role.ROOT)

    messages = []

    # Message 1: User corrects and adds details to existing OpenViking project card
    msg1 = Message(
        id="msg1",
        role="user",
        parts=[
            TextPart(
                "I just looked at our OpenViking project card and need to correct it: "
                "OpenViking is not just a context database, it's an Agent-native memory system, "
                "supporting multi-level memory (L0/L1/L2) and incremental updates. "
                "Also, in the technical approach section, we not only use the ExtractLoop pattern, "
                "but also need to support schema-driven memory extraction."
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
                "Okay, I'll update the project card! Does the ExtractLoop pattern description need adjustment too?"
            )
        ],
    )
    messages.append(msg2)

    # Message 3: User updates ExtractLoop card and adds to event
    msg3 = Message(
        id="msg3",
        role="user",
        parts=[
            TextPart(
                "Yes, the ExtractLoop card also needs updating: ExtractLoop is not just about analyzing conversations and generating operations, "
                "it's a complete orchestrator responsible for tool calling, LLM reasoning, and memory operation integration. "
                "Also, the event card that mentions 'Decided to use ExtractLoop pattern' "
                "needs to add the reason: because the ExtractLoop pattern can handle uncertainty well, "
                "verifying the correctness of memory operations through the ReAct flow."
            )
        ],
    )
    messages.append(msg3)

    return messages


class TestMemoryExtractorFlow:
    """Test the complete memory extraction flow."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_flow_with_real_llm(self):
        # Only mock VikingFS, everything else is real!
        viking_fs = MockVikingFS()
        initialize_openviking_config()
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        print(f"vlm={vlm}")
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)

        # Create test conversation
        messages = create_test_conversation()

        # Format conversation as string
        conversation_str = "\n".join([f"[{msg.role}]: {msg.content}" for msg in messages])

        print("-" * 60)
        print("使用真实 LLM 测试完整流程（cards & events）...")
        print("对话内容：")
        print(conversation_str[:800] + "..." if len(conversation_str) > 800 else conversation_str)
        print("-" * 60)

        # Initialize orchestrator with real VLM!
        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
        )

        # Take snapshot BEFORE running orchestrator to capture all changes
        viking_fs.snapshot()

        # Actually run the orchestrator with real LLM calls!
        operations, tools_used = await orchestrator.run(
            messages=messages,
        )

        # Verify results
        assert operations is not None
        assert tools_used is not None

        print("-" * 60)
        print("生成的操作：")
        print(f"  写入：{len(operations.write_uris)}")
        print(f"  编辑：{len(operations.edit_uris)}")
        print(f"  删除：{len(operations.delete_uris)}")
        print(f"  使用的工具：{len(tools_used)}")
        print("-" * 60)

        # Now test MemoryUpdater with the operations, mock get_viking_fs
        with patch(
            "openviking.session.memory.memory_updater.get_viking_fs", return_value=viking_fs
        ):
            updater = MemoryUpdater()
            # Pass the registry from orchestrator
            result = await updater.apply_operations(operations, ctx, registry=orchestrator.registry)

            assert isinstance(result, MemoryUpdateResult)

            print("已应用的操作：")
            print(f"  已写入：{len(result.written_uris)}")
            print(f"  已编辑：{len(result.edited_uris)}")
            print(f"  已删除：{len(result.deleted_uris)}")
            print(f"  错误：{len(result.errors)}")
            print("-" * 60)

            # Print diff since snapshot
            diff = viking_fs.diff_since_snapshot()
            print_diff(diff)

        # Check that at least something happened (could be write/edit/delete depending on LLM)
        total_changes = (
            len(operations.write_uris) + len(operations.edit_uris) + len(operations.delete_uris)
        )
        print(f"LLM 建议的总变更数：{total_changes}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_existing_memories_with_real_llm(self):
        """Test updating existing cards and events with real LLM (only VikingFS is mocked)."""
        # Check if VLM is available
        initialize_openviking_config()
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        print(f"vlm={vlm}")

        # Only mock VikingFS, everything else is real!
        viking_fs = MockVikingFS()
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)

        existing_memories = create_existing_memories_content()
        for uri, content in existing_memories.items():
            await viking_fs.write_file(uri, content)

        # Create test conversation for updating
        messages = create_update_conversation()

        # Format conversation as string
        conversation_str = "\n".join([f"[{msg.role}]: {msg.content}" for msg in messages])

        print("=" * 60)
        print("测试更新已有 cards 和 events...")
        print("-" * 60)
        print("已有记忆内容：")
        for uri, content in existing_memories.items():
            print(f"\n--- {uri} ---")
            print(content[:300] + "..." if len(content) > 300 else content)
        print("-" * 60)
        print("新对话内容：")
        print(conversation_str)
        print("=" * 60)

        # Initialize orchestrator with real VLM!
        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
        )

        # Take snapshot BEFORE running orchestrator to capture all changes
        viking_fs.snapshot()

        # Actually run the orchestrator with real LLM calls!
        operations, tools_used = await orchestrator.run(
            messages=messages,
        )

        # Verify results
        assert operations is not None
        assert tools_used is not None
        print(f"operations={operations.model_dump_json(indent=4)}")
        print("=" * 60)
        print("生成的操作：")
        print(f"  写入：{len(operations.write_uris)}")
        print(f"  编辑：{len(operations.edit_uris)}")
        print(f"  删除：{len(operations.delete_uris)}")
        print(f"  使用的工具：{len(tools_used)}")

        if operations.edit_uris:
            print("\n编辑操作详情：")
            for op in operations.edit_uris:
                # Handle both dict and model objects
                if isinstance(op, dict):
                    print(f"  - memory_type: {op.get('memory_type', 'unknown')}")
                    if "fields" in op:
                        print(f"  - fields: {op['fields']}")
                    if "patches" in op:
                        print(f"    补丁：{list(op['patches'].keys())}")
                    if "content" in op:
                        print(f"  - content: {str(op['content'])[:100]}...")
                else:
                    # Try to access as model attributes
                    memory_type = getattr(op, "memory_type", "unknown")
                    print(f"  - memory_type: {memory_type}")
                    fields = getattr(op, "fields", None)
                    if fields:
                        print(f"  - fields: {fields}")
                    patches = getattr(op, "patches", None)
                    if patches:
                        print(f"    补丁：{list(patches.keys())}")
                    content = getattr(op, "content", None)
                    if content:
                        print(f"  - content: {str(content)[:100]}...")

        print("=" * 60)

        # Now test MemoryUpdater with the operations, mock get_viking_fs
        with patch(
            "openviking.session.memory.memory_updater.get_viking_fs", return_value=viking_fs
        ):
            updater = MemoryUpdater()
            # Pass the registry from orchestrator
            result = await updater.apply_operations(operations, ctx, registry=orchestrator.registry)

            assert isinstance(result, MemoryUpdateResult)

            print("已应用的操作：")
            print(f"  已写入：{len(result.written_uris)}")
            print(f"  已编辑：{len(result.edited_uris)}")
            print(f"  已删除：{len(result.deleted_uris)}")
            print(f"  错误：{len(result.errors)}")
            print("=" * 60)

            # Print diff since snapshot
            diff = viking_fs.diff_since_snapshot()
            print_diff(diff)

        # Check updated content
        print("\n更新后的记忆内容：")
        for uri in existing_memories.keys():
            new_content = await viking_fs.read_file(uri)
            if new_content != existing_memories.get(uri, ""):
                print(f"\n--- {uri} (已更新) ---")
                print(new_content[:500] + "..." if len(new_content) > 500 else new_content)
            else:
                print(f"\n--- {uri} (未变化) ---")
        # Also check if new cards/events were created
        print("\n--- cards 目录内容 ---")
        try:
            card_files = await viking_fs.ls("viking://agent/default/memories/cards")
            for f in card_files:
                print(f"  - {f.get('name', 'unknown')}")
        except Exception as e:
            print(f"  无法列出目录: {e}")
        print("\n--- events 目录内容 ---")
        try:
            event_files = await viking_fs.ls("viking://user/default/memories/events")
            for f in event_files:
                print(f"  - {f.get('name', 'unknown')}")
        except Exception as e:
            print(f"  无法列出目录: {e}")
        print("=" * 60)

        # Check that at least something happened (could be write/edit/delete depending on LLM)
        total_changes = (
            len(operations.write_uris) + len(operations.edit_uris) + len(operations.delete_uris)
        )
        print(f"LLM 建议的总变更数：{total_changes}")

    def test_message_formatting(self):
        """Test that messages can be formatted correctly."""
        messages = create_test_conversation()

        assert len(messages) == 5
        assert messages[0].role == "user"
        assert "OpenViking" in messages[0].content
        assert "memory extraction" in messages[0].content
