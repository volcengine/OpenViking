# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for _process_memory_directory recursive subdirectory handling.

Verifies that:
1. recursive=True processes subdirectories recursively (reindex path).
2. recursive=False does NOT process subdirectories (write path unchanged).
3. skip_vectorization is propagated to child messages.
4. Hidden directories (starting with '.') are skipped.
5. Child directory failures do not block parent processing (error isolation).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _make_processor() -> SemanticProcessor:
    """Create a SemanticProcessor with mocked internals."""
    proc = SemanticProcessor(max_concurrent_llm=4)
    return proc


def _make_msg(
    uri: str = "viking://user/alice/memories/entities",
    recursive: bool = True,
    skip_vectorization: bool = False,
) -> SemanticMsg:
    return SemanticMsg(
        uri=uri,
        context_type="memory",
        recursive=recursive,
        skip_vectorization=skip_vectorization,
        account_id="acme",
        user_id="alice",
        peer_id="default",
        role="root",
    )


class _FakeFS:
    """Fake VikingFS that returns configurable directory listings."""

    def __init__(self, listings: dict[str, list[dict]]):
        self._listings = listings
        self.ls_calls: list[str] = []

    async def ls(self, uri, node_limit=None, ctx=None):
        self.ls_calls.append(uri)
        return self._listings.get(uri, [])

    async def read_file(self, uri, ctx=None):
        return ""


def _patch_heavy_methods(proc: SemanticProcessor):
    """Return a list of patch context managers for heavy methods."""
    return [
        patch.object(proc, "_generate_single_file_summary", new_callable=AsyncMock),
        patch.object(proc, "_generate_overview", new_callable=AsyncMock, return_value="overview"),
        patch.object(proc, "_normalize_overview_generation", return_value=("overview", "abstract")),
        patch.object(
            proc, "_write_memory_directory_semantics", new_callable=AsyncMock, return_value=True
        ),
        patch.object(proc, "_vectorize_single_file", new_callable=AsyncMock),
        patch.object(proc, "_vectorize_directory", new_callable=AsyncMock),
    ]


class TestRecursiveSubdirectoryProcessing:
    """Test that recursive=True triggers subdirectory processing."""

    @pytest.mark.asyncio
    async def test_recursive_true_processes_subdirs(self):
        """When recursive=True, subdirectories are recursively processed."""
        proc = _make_processor()
        root_uri = "viking://user/alice/memories/entities"
        child1 = "viking://user/alice/memories/entities/projects"
        child2 = "viking://user/alice/memories/entities/people"

        fs = _FakeFS(
            {
                root_uri: [
                    {"name": "projects", "isDir": True},
                    {"name": "people", "isDir": True},
                    {"name": "readme.md", "isDir": False},
                ],
                child1: [{"name": "illuminator.md", "isDir": False}],
                child2: [{"name": "alice.md", "isDir": False}],
            }
        )

        patches = _patch_heavy_methods(proc)
        with (
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker",
            ),
        ):
            for p in patches:
                p.start()
            try:
                msg = _make_msg(uri=root_uri, recursive=True)
                await proc._process_memory_directory(msg)
            finally:
                for p in patches:
                    p.stop()

        # All three directories should have been listed (root + 2 children)
        assert root_uri in fs.ls_calls
        assert child1 in fs.ls_calls
        assert child2 in fs.ls_calls

    @pytest.mark.asyncio
    async def test_recursive_false_skips_subdirs(self):
        """When recursive=False (write path), subdirectories are NOT processed."""
        proc = _make_processor()
        root_uri = "viking://user/alice/memories/entities"

        fs = _FakeFS(
            {
                root_uri: [
                    {"name": "projects", "isDir": True},
                    {"name": "readme.md", "isDir": False},
                ],
                "viking://user/alice/memories/entities/projects": [
                    {"name": "illuminator.md", "isDir": False},
                ],
            }
        )

        patches = _patch_heavy_methods(proc)
        with (
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker",
            ),
        ):
            for p in patches:
                p.start()
            try:
                msg = _make_msg(uri=root_uri, recursive=False)
                await proc._process_memory_directory(msg)
            finally:
                for p in patches:
                    p.stop()

        # Only root should be listed, NOT the child
        assert root_uri in fs.ls_calls
        assert "viking://user/alice/memories/entities/projects" not in fs.ls_calls

    @pytest.mark.asyncio
    async def test_skip_vectorization_propagated(self):
        """skip_vectorization flag is propagated to child messages."""
        proc = _make_processor()
        root_uri = "viking://user/alice/memories/entities"
        child_uri = "viking://user/alice/memories/entities/projects"

        fs = _FakeFS(
            {
                root_uri: [{"name": "projects", "isDir": True}],
                child_uri: [{"name": "file.md", "isDir": False}],
            }
        )

        # Track child messages by intercepting _process_memory_directory calls
        child_msgs: list[SemanticMsg] = []
        original = proc._process_memory_directory

        async def spy(msg, ctx=None, lock=None):
            if msg.uri != root_uri:
                child_msgs.append(msg)
                return
            return await original(msg, ctx=ctx, lock=lock)

        patches = _patch_heavy_methods(proc)
        with (
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker",
            ),
            patch.object(proc, "_process_memory_directory", side_effect=spy),
        ):
            for p in patches:
                p.start()
            try:
                msg = _make_msg(uri=root_uri, recursive=True, skip_vectorization=True)
                await proc._process_memory_directory(msg)
            finally:
                for p in patches:
                    p.stop()

        assert len(child_msgs) == 1
        assert child_msgs[0].skip_vectorization is True
        assert child_msgs[0].recursive is True
        assert child_msgs[0].account_id == "acme"
        assert child_msgs[0].user_id == "alice"
        assert child_msgs[0].peer_id == "default"

    @pytest.mark.asyncio
    async def test_hidden_dirs_skipped(self):
        """Hidden directories (starting with '.') are not recursed into."""
        proc = _make_processor()
        root_uri = "viking://user/alice/memories/entities"

        fs = _FakeFS(
            {
                root_uri: [
                    {"name": ".hidden", "isDir": True},
                    {"name": "visible", "isDir": True},
                ],
                "viking://user/alice/memories/entities/visible": [
                    {"name": "file.md", "isDir": False},
                ],
            }
        )

        patches = _patch_heavy_methods(proc)
        with (
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker",
            ),
        ):
            for p in patches:
                p.start()
            try:
                msg = _make_msg(uri=root_uri, recursive=True)
                await proc._process_memory_directory(msg)
            finally:
                for p in patches:
                    p.stop()

        # visible dir processed, .hidden dir NOT processed
        assert "viking://user/alice/memories/entities/visible" in fs.ls_calls
        assert "viking://user/alice/memories/entities/.hidden" not in fs.ls_calls

    @pytest.mark.asyncio
    async def test_child_failure_does_not_block_parent(self):
        """A failing child directory does not prevent parent from completing."""
        proc = _make_processor()
        root_uri = "viking://user/alice/memories/entities"
        bad_child = "viking://user/alice/memories/entities/bad"
        good_child = "viking://user/alice/memories/entities/good"

        fs = _FakeFS(
            {
                root_uri: [
                    {"name": "bad", "isDir": True},
                    {"name": "good", "isDir": True},
                    {"name": "file.md", "isDir": False},
                ],
                good_child: [{"name": "ok.md", "isDir": False}],
            }
        )

        # Make ls raise for bad_child
        original_ls = fs.ls

        async def failing_ls(uri, node_limit=None, ctx=None):
            if uri == bad_child:
                raise RuntimeError("simulated failure")
            return await original_ls(uri, node_limit=node_limit, ctx=ctx)

        fs.ls = failing_ls

        patches = _patch_heavy_methods(proc)
        with (
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker",
            ),
        ):
            for p in patches:
                p.start()
            try:
                msg = _make_msg(uri=root_uri, recursive=True)
                # Should NOT raise despite bad child failure
                await proc._process_memory_directory(msg)
            finally:
                for p in patches:
                    p.stop()

        # good_child was still processed
        assert good_child in fs.ls_calls
        # root was processed (file.md exists)
        assert root_uri in fs.ls_calls
