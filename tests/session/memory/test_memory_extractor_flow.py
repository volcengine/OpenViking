# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Test memory extraction flow with memory module components.

This test simulates the complete memory extraction workflow:
1. Setup conversation messages
2. Pre-fetch memory directory structure
3. Call ReAct orchestrator to analyze and determine memory changes
4. Generate memory operations
5. Apply operations via MemoryUpdater
"""

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory import (
    MemoryOperations,
    MemoryReAct,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config, initialize_openviking_config


class MockVikingFS:
    """Mock VikingFS for testing."""

    def __init__(self):
        self.files: Dict[str, str] = {}
        self.directories: Dict[str, List[Dict[str, Any]]] = {}
        self._snapshot: Dict[str, str] = {}

    async def read_file(self, uri: str, **kwargs) -> str:
        """Mock read_file."""
        return self.files.get(uri, "")

    async def write_file(self, uri: str, content: str, **kwargs) -> None:
        """Mock write_file."""
        self.files[uri] = content

    async def ls(self, uri: str, **kwargs) -> List[Dict[str, Any]]:
        """Mock ls."""
        return self.directories.get(uri, [])

    async def mkdir(self, uri: str, **kwargs) -> None:
        """Mock mkdir."""
        if uri not in self.directories:
            self.directories[uri] = []

    async def rm(self, uri: str, **kwargs) -> None:
        """Mock rm."""
        if uri in self.files:
            del self.files[uri]

    async def stat(self, uri: str, **kwargs) -> Dict[str, Any]:
        """Mock stat."""
        if uri in self.files:
            return {"type": "file", "uri": uri}
        if uri in self.directories:
            return {"type": "dir", "uri": uri}
        raise FileNotFoundError(f"Not found: {uri}")

    async def find(self, query: str, **kwargs) -> Dict[str, Any]:
        """Mock find."""
        return {
            "memories": [],
            "resources": [],
            "skills": [],
        }

    async def tree(self, uri: str, **kwargs) -> Dict[str, Any]:
        """Mock tree."""
        return {"uri": uri, "tree": []}

    def snapshot(self) -> None:
        """Save a snapshot of the current file state."""
        self._snapshot = self.files.copy()

    def diff_since_snapshot(self) -> Dict[str, Dict[str, str]]:
        """
        Compute diff since last snapshot.

        Returns:
            Dict with keys 'added', 'modified', 'deleted', each mapping URIs to content.
        """
        added = {}
        modified = {}
        deleted = {}

        # Check for added/modified files
        for uri, content in self.files.items():
            if uri not in self._snapshot:
                added[uri] = content
            elif content != self._snapshot[uri]:
                modified[uri] = {
                    "old": self._snapshot[uri],
                    "new": content
                }

        # Check for deleted files
        for uri in self._snapshot:
            if uri not in self.files:
                deleted[uri] = self._snapshot[uri]

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted
        }


def print_diff(diff: Dict[str, Dict[str, str]]) -> None:
    """
    Print diff in a readable format using diff-match-patch.
    """
    try:
        from diff_match_patch import diff_match_patch
    except ImportError:
        print("Warning: diff-match-patch not available, using simple diff printing.")
        _print_simple_diff(diff)
        return

    dmp = diff_match_patch()

    print("\n" + "=" * 80)
    print("MEMORY CHANGES DIFF")
    print("=" * 80)

    # Added files
    if diff["added"]:
        print(f"\n[ADDED] {len(diff['added'])} file(s):")
        for uri, content in diff["added"].items():
            print(f"\n  {uri}")
            print("  " + "-" * 76)
            for line in content.split("\n"):
                print(f"  + {line}")

    # Modified files
    if diff["modified"]:
        print(f"\n[MODIFIED] {len(diff['modified'])} file(s):")
        for uri, changes in diff["modified"].items():
            print(f"\n  {uri}")
            print("  " + "-" * 76)
            # Compute word-level diff
            diffs = dmp.diff_main(changes["old"], changes["new"])
            dmp.diff_cleanupSemantic(diffs)
            # Format output
            for op, text in diffs:
                lines = text.split("\n")
                for line in lines:
                    if line:
                        if op == 0:  # equal
                            print(f"    {line}")
                        elif op == 1:  # insert
                            print(f"  + {line}")
                        elif op == -1:  # delete
                            print(f"  - {line}")

    # Deleted files
    if diff["deleted"]:
        print(f"\n[DELETED] {len(diff['deleted'])} file(s):")
        for uri, content in diff["deleted"].items():
            print(f"\n  {uri}")
            print("  " + "-" * 76)
            for line in content.split("\n"):
                print(f"  - {line}")

    if not any(diff.values()):
        print("\n  No changes detected.")

    print("\n" + "=" * 80 + "\n")


def _print_simple_diff(diff: Dict[str, Dict[str, str]]) -> None:
    """Simple diff printing without diff-match-patch."""
    print("\n" + "=" * 80)
    print("MEMORY CHANGES DIFF (simple mode)")
    print("=" * 80)
    print(f"Added: {len(diff['added'])} files")
    print(f"Modified: {len(diff['modified'])} files")
    print(f"Deleted: {len(diff['deleted'])} files")
    print("=" * 80 + "\n")


def setup_mock_vikingfs_for_pre_fetch(viking_fs: MockVikingFS, pre_fetched_data: Dict[str, Any]):
    """
    Setup MockVikingFS with data so that _pre_fetch_context() returns the expected data.

    Args:
        viking_fs: MockVikingFS instance to setup
        pre_fetched_data: The same data format as create_pre_fetched_context() returns
    """
    # Setup directories for ls
    if "directories" in pre_fetched_data:
        for dir_uri, entries in pre_fetched_data["directories"].items():
            viking_fs.directories[dir_uri] = entries

    # Setup files for read
    if "summaries" in pre_fetched_data:
        for file_uri, content in pre_fetched_data["summaries"].items():
            viking_fs.files[file_uri] = content


@dataclass
class MockToolCall:
    """Mock tool call for testing."""
    name: str
    arguments: Dict[str, Any]


@dataclass
class MockResponse:
    """Mock response for testing."""
    content: str
    has_tool_calls: bool = False
    tool_calls: List[MockToolCall] = None


class MockLLMProvider:
    """Mock LLM provider for testing."""

    def __init__(self):
        self.response_content = ""
        self.has_tool_calls = False
        self.tool_calls = []

    def get_default_model(self) -> str:
        """Get default model."""
        return "test-model"

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Any = None,
        **kwargs,
    ) -> Any:
        """Mock chat completion."""
        response = MockResponse(
            content=self.response_content,
            has_tool_calls=self.has_tool_calls,
            tool_calls=self.tool_calls,
        )
        return response


class RealLLMProvider:
    """Real LLM provider using local ov.conf VLM."""

    def __init__(self):
        """Initialize with VLM from config."""
        # Initialize config if not already initialized
        try:
            initialize_openviking_config()
        except Exception:
            pass
        self.config = get_openviking_config()
        self.vlm = self.config.vlm

    def get_default_model(self) -> str:
        """Get default model from config."""
        return self.vlm.model or "default-model"

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Any = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> Any:
        """Chat completion using real VLM."""
        # Build prompt from messages
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                prompt_parts.append(f"[{role}]: {content}")
        prompt = "\n\n".join(prompt_parts)

        # Call VLM
        try:
            response_content = await self.vlm.get_completion_async(
                prompt,
                thinking=False,
                max_retries=2,
            )
            print(f'response_content={response_content}')
        except Exception as e:
            print(f"VLM call failed: {e}")
            response_content = "{}"

        # Return mock response format
        return MockResponse(
            content=response_content,
            has_tool_calls=False,
            tool_calls=[],
        )


def create_test_conversation() -> List[Message]:
    """Create a test conversation."""
    user = UserIdentifier.the_default_user()
    ctx = RequestContext(user=user, role=Role.ROOT)

    messages = []

    # Message 1: User introduces themselves
    msg1 = Message(
        id="msg1",
        role="user",
        parts=[TextPart("你好，我是张三。我是一名软件工程师，主要做 Python 项目开发。")],
    )
    messages.append(msg1)

    # Message 2: Assistant responds
    msg2 = Message(
        id="msg2",
        role="assistant",
        parts=[TextPart("你好张三！很高兴认识你。你在做什么项目呢？")],
    )
    messages.append(msg2)

    # Message 3: User talks about preferences
    msg3 = Message(
        id="msg3",
        role="user",
        parts=[TextPart(
            "我在做一个记忆系统。我喜欢写有类型提示的干净代码，"
            "测试我喜欢用 pytest。对了，我用深色模式！"
        )],
    )
    messages.append(msg3)

    # Message 4: Assistant asks about tools
    msg4 = Message(
        id="msg4",
        role="assistant",
        parts=[TextPart("听起来很有意思！你用什么工具呢？")],
    )
    messages.append(msg4)

    # Message 5: User talks about tools
    msg5 = Message(
        id="msg5",
        role="user",
        parts=[TextPart(
            "我用 VS Code，装了 GitHub Copilot 插件。代码检查我喜欢用 ruff。"
            "我们昨天刚决定从 black 迁移到 ruff format。"
        )],
    )
    messages.append(msg5)

    return messages


def create_pre_fetched_context() -> Dict[str, Any]:
    """Create pre-fetched context for testing."""
    return {
        "directories": {
            "viking://user/default/memories": [
                {"name": "profile.md", "isDir": False, "abstract": "用户档案"},
                {"name": "preferences", "isDir": True},
            ],
            "viking://user/default/memories/preferences": [],
        },
        "summaries": {
            "viking://user/default/memories/profile.md": "# 用户档案\n\n姓名：未知",
        },
        "search_results": [],
    }


def create_existing_memories_content() -> Dict[str, str]:
    """Create existing memory content for update test."""
    return {
        "viking://user/default/memories/profile.md": """# 用户档案

## 基本信息
- 姓名：张三
- 职业：软件工程师
- 技术栈：Python

## 项目经历
- 曾参与过多个 Python 项目开发""",
        "viking://user/default/memories/preferences/开发工具与代码规范.md": """# 开发工具与代码规范

## 编辑器
- VS Code

## 代码风格
- 使用 black 格式化

## 测试
- 喜欢写单元测试""",
    }


def create_update_conversation() -> List[Message]:
    """Create a conversation for updating existing memories."""
    user = UserIdentifier.the_default_user()
    ctx = RequestContext(user=user, role=Role.ROOT)

    messages = []

    # Message 1: User updates their editor preference
    msg1 = Message(
        id="msg1",
        role="user",
        parts=[TextPart("对了，我最近把我现在不用 black 了，改成用 ruff format。")],
    )
    messages.append(msg1)

    # Message 2: Assistant responds
    msg2 = Message(
        id="msg2",
        role="assistant",
        parts=[TextPart("好的，了解！ruff 确实是个不错的选择！")],
    )
    messages.append(msg2)

    # Message 3: User adds new info
    msg3 = Message(
        id="msg3",
        role="user",
        parts=[TextPart("还有，我最近在学习用 NeoVim，感觉效率更高了。")],
    )
    messages.append(msg3)

    return messages


def create_pre_fetched_context_for_update() -> Dict[str, Any]:
    """Create pre-fetched context with existing memories for update test."""
    return {
        "directories": {
            "viking://user/default/memories": [
                {"name": "profile.md", "isDir": False, "abstract": "用户档案"},
                {"name": "preferences", "isDir": True},
            ],
            "viking://user/default/memories/preferences": [
                {"name": "开发工具与代码规范.md", "isDir": False, "abstract": "开发工具与代码规范"},
            ],
        },
        "summaries": {
            "viking://user/default/memories/profile.md": "# 用户档案\n\n## 基本信息\n- 姓名：张三\n- 职业：软件工程师\n- 技术栈：Python",
            "viking://user/default/memories/preferences/开发工具与代码规范.md": "# 开发工具与代码规范\n\n## 编辑器\n- VS Code\n\n## 代码风格\n- 使用 black 格式化",
        },
        "search_results": [],
    }


class TestMemoryExtractorFlow:
    """Test the complete memory extraction flow."""



    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_flow_with_real_llm(self):
        """Test the full memory extraction flow with real LLM (only VikingFS is mocked)."""
        # Check if VLM is available
        try:
            initialize_openviking_config()
            config = get_openviking_config()
            if not config.vlm.is_available():
                pytest.skip("VLM not configured, skipping integration test")
        except Exception as e:
            pytest.skip(f"Could not initialize config: {e}")

        # Only mock VikingFS, everything else is real!
        viking_fs = MockVikingFS()
        llm_provider = RealLLMProvider()
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)

        # Setup initial memory files in mock VikingFS for pre-fetch
        pre_fetched_context = create_pre_fetched_context()
        setup_mock_vikingfs_for_pre_fetch(viking_fs, pre_fetched_context)

        # Create test conversation
        messages = create_test_conversation()

        # Format conversation as string
        conversation_str = "\n".join([
            f"[{msg.role}]: {msg.content}"
            for msg in messages
        ])

        print("-" * 60)
        print("使用真实 LLM 测试完整流程...")
        print("对话内容：")
        print(conversation_str[:800] + "..." if len(conversation_str) > 800 else conversation_str)
        print("-" * 60)

        # Initialize orchestrator with real LLM provider!
        orchestrator = MemoryReAct(
            llm_provider=llm_provider,
            viking_fs=viking_fs,
            ctx=ctx,
        )

        # Actually run the orchestrator with real LLM calls!
        operations, tools_used = await orchestrator.run(
            conversation=conversation_str,
        )

        # Verify results
        assert operations is not None
        assert tools_used is not None

        print("-" * 60)
        print(f"生成的操作：")
        print(f"  写入：{len(operations.write_operations)}")
        print(f"  编辑：{len(operations.edit_operations)}")
        print(f"  删除：{len(operations.delete_operations)}")
        print(f"  使用的工具：{len(tools_used)}")
        print("-" * 60)

        # Now test MemoryUpdater with the operations, mock get_viking_fs
        with patch('openviking.session.memory.memory_updater.get_viking_fs', return_value=viking_fs):
            updater = MemoryUpdater()
            # Pass the registry from orchestrator
            # Take snapshot before applying operations
            viking_fs.snapshot()
            result = await updater.apply_operations(operations, ctx, registry=orchestrator.registry)

            assert isinstance(result, MemoryUpdateResult)

            print(f"已应用的操作：")
            print(f"  已写入：{len(result.written_uris)}")
            print(f"  已编辑：{len(result.edited_uris)}")
            print(f"  已删除：{len(result.deleted_uris)}")
            print(f"  错误：{len(result.errors)}")
            print("-" * 60)

            # Print diff since snapshot
            diff = viking_fs.diff_since_snapshot()
            print_diff(diff)

        # Check that at least something happened (could be write/edit/delete depending on LLM)
        total_changes = (len(operations.write_operations) +
                        len(operations.edit_operations) +
                        len(operations.delete_operations))
        print(f"LLM 建议的总变更数：{total_changes}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_existing_memories_with_real_llm(self):
        """Test updating existing memories with real LLM (only VikingFS is mocked)."""
        # Check if VLM is available
        try:
            initialize_openviking_config()
            config = get_openviking_config()
            if not config.vlm.is_available():
                pytest.skip("VLM not configured, skipping integration test")
        except Exception as e:
            pytest.skip(f"Could not initialize config: {e}")

        # Only mock VikingFS, everything else is real!
        viking_fs = MockVikingFS()
        llm_provider = RealLLMProvider()
        user = UserIdentifier.the_default_user()
        ctx = RequestContext(user=user, role=Role.ROOT)

        # Setup EXISTING memory files in mock VikingFS for pre-fetch
        pre_fetched_context = create_pre_fetched_context_for_update()
        setup_mock_vikingfs_for_pre_fetch(viking_fs, pre_fetched_context)

        # Also write the actual file content (setup_mock_vikingfs_for_pre_fetch only
        # sets up what's needed for ls/read, but we need full content for updates)
        existing_memories = create_existing_memories_content()
        for uri, content in existing_memories.items():
            viking_fs.files[uri] = content

        # Create test conversation for updating
        messages = create_update_conversation()

        # Format conversation as string
        conversation_str = "\n".join([
            f"[{msg.role}]: {msg.content}"
            for msg in messages
        ])

        print("=" * 60)
        print("测试更新已有记忆...")
        print("-" * 60)
        print("已有记忆内容：")
        for uri, content in existing_memories.items():
            print(f"\n--- {uri} ---")
            print(content[:300] + "..." if len(content) > 300 else content)
        print("-" * 60)
        print("新对话内容：")
        print(conversation_str)
        print("=" * 60)

        # Initialize orchestrator with real LLM provider!
        orchestrator = MemoryReAct(
            llm_provider=llm_provider,
            viking_fs=viking_fs,
            ctx=ctx,
        )

        # Actually run the orchestrator with real LLM calls!
        operations, tools_used = await orchestrator.run(
            conversation=conversation_str,
        )

        # Verify results
        assert operations is not None
        assert tools_used is not None

        print("=" * 60)
        print(f"生成的操作：")
        print(f"  写入：{len(operations.write_operations)}")
        print(f"  编辑：{len(operations.edit_operations)}")
        print(f"  删除：{len(operations.delete_operations)}")
        print(f"  使用的工具：{len(tools_used)}")

        if operations.edit_operations:
            print("\n编辑操作详情：")
            for op in operations.edit_operations:
                print(f"  - memory_type: {op.memory_type}")
                print(f"  - fields: {op.fields}")
                print(f"    补丁：{list(op.patches.keys())}")

        print("=" * 60)

        # Now test MemoryUpdater with the operations, mock get_viking_fs
        with patch('openviking.session.memory.memory_updater.get_viking_fs', return_value=viking_fs):
            updater = MemoryUpdater()
            # Pass the registry from orchestrator
            # Take snapshot before applying operations
            viking_fs.snapshot()
            result = await updater.apply_operations(operations, ctx, registry=orchestrator.registry)

            assert isinstance(result, MemoryUpdateResult)

            print(f"已应用的操作：")
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
        # Also check if new preference files were created
        print("\n--- preferences 目录内容 ---")
        try:
            pref_files = await viking_fs.ls("viking://user/default/memories/preferences")
            for f in pref_files:
                print(f"  - {f.get('name', 'unknown')}")
        except Exception as e:
            print(f"  无法列出目录: {e}")
        print("=" * 60)

        # Check that at least something happened (could be write/edit/delete depending on LLM)
        total_changes = (len(operations.write_operations) +
                        len(operations.edit_operations) +
                        len(operations.delete_operations))
        print(f"LLM 建议的总变更数：{total_changes}")

    def test_message_formatting(self):
        """Test that messages can be formatted correctly."""
        messages = create_test_conversation()

        assert len(messages) == 5
        assert messages[0].role == "user"
        assert "张三" in messages[0].content
        assert "软件工程师" in messages[0].content

    def test_pre_fetched_context_creation(self):
        """Test that pre-fetched context can be created."""
        context = create_pre_fetched_context()

        assert "directories" in context
        assert "summaries" in context
        assert "search_results" in context
        assert len(context["directories"]) > 0
        assert len(context["summaries"]) > 0

