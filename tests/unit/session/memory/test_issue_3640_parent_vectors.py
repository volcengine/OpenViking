# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for issue #3640: SessionCommit can leave memory parent directories
without vector records.

These tests live under tests/unit/session/memory so they run without the
session-level ``client`` autouse fixture (which requires a live AGFS server).
They verify three behaviours in isolation:

1. ``initialize_memory_files`` returns only URIs it newly created.
2. ``MemoryUpdater.initialize_and_vectorize_default_files`` (used by
   compressor v2/v3) vectorizes only those newly created URIs.
3. ``apply_operations`` emits exactly one L1 OVERVIEW record per generated
   parent directory (via ``vectorize_directory_meta(include_abstract=False)``)
   and does not add ``.overview.md`` to ``written_uris`` or emit any L0/L2
   confusion.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    FieldType,
    MemoryField,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)


# ──────────────────────────────────────────────────────────────────────────────
# 1. initialize_memory_files returns only newly created URIs
# ──────────────────────────────────────────────────────────────────────────────


class _FakeFSInit:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def read_file(self, uri, ctx=None):
        if uri in self.files:
            return self.files[uri]
        raise FileNotFoundError(uri)

    async def write_file(self, uri, data, ctx=None, **_kw):
        self.files[uri] = data


class TestInitializeMemoryFilesReturn:
    @pytest.mark.asyncio
    async def test_returns_only_newly_created_uris(self):
        fs = _FakeFSInit()
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types = {
            "identity": MemoryTypeSchema(
                memory_type="identity",
                description="identity",
                directory="viking://user/{{ user_space }}/memories/identity",
                filename_template="identity.md",
                content_template="identity file",
                fields=[MemoryField(name="name", field_type=FieldType.STRING, init_value="alice")],
                enabled=True,
            ),
            "soul": MemoryTypeSchema(
                memory_type="soul",
                description="soul",
                directory="viking://user/{{ user_space }}/memories/soul",
                filename_template="soul.md",
                content_template="soul file",
                fields=[
                    MemoryField(name="purpose", field_type=FieldType.STRING, init_value="be kind")
                ],
                enabled=True,
            ),
        }

        with patch(
            "openviking.storage.viking_fs.get_viking_fs",
            return_value=fs,
        ):
            first = await registry.initialize_memory_files(_ctx())
            assert len(first) == 2
            assert any("identity.md" in u for u in first)
            assert any("soul.md" in u for u in first)

            # Pre-existing files are skipped and not returned.
            second = await registry.initialize_memory_files(_ctx())
            assert second == []
            assert len(fs.files) == 2

    @pytest.mark.asyncio
    async def test_skips_schemas_without_init_value_fields(self):
        fs = _FakeFSInit()
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types = {
            "events": MemoryTypeSchema(
                memory_type="events",
                description="events",
                directory="viking://user/{{ user_space }}/memories/events",
                filename_template="{{ name }}.md",
                fields=[MemoryField(name="content", field_type=FieldType.STRING)],
                enabled=True,
            ),
        }
        with patch(
            "openviking.storage.viking_fs.get_viking_fs",
            return_value=fs,
        ):
            assert await registry.initialize_memory_files(_ctx()) == []
        assert fs.files == {}


# ──────────────────────────────────────────────────────────────────────────────
# 2. v2/v3 initialize_and_vectorize_default_files vectorizes only new URIs
# ──────────────────────────────────────────────────────────────────────────────


class TestInitializeAndVectorizeDefaultFiles:
    @pytest.mark.asyncio
    async def test_vectorizes_each_new_uri_via_refresh_file_embedding(self):
        vikingdb = MagicMock()
        vikingdb.has_queue_manager = True

        async def _fake_init(_ctx, allowed_memory_types=None):
            return [
                "viking://user/alice/memories/identity/identity.md",
                "viking://user/alice/memories/soul/soul.md",
            ]

        with (
            patch(
                "openviking.session.memory.memory_type_registry.create_default_registry"
            ) as mock_registry_factory,
            patch(
                "openviking.session.memory.memory_updater.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
            ) as mock_refresh,
            patch("openviking.storage.viking_fs.get_viking_fs") as mock_get_fs,
        ):
            fake_registry = MagicMock()
            fake_registry.initialize_memory_files = _fake_init
            mock_registry_factory.return_value = fake_registry
            mock_get_fs.return_value = MagicMock()

            created = await MemoryUpdater.initialize_and_vectorize_default_files(
                vikingdb=vikingdb,
                ctx=_ctx(),
            )

            assert len(created) == 2
            assert mock_refresh.await_count == 2
            refreshed_uris = [c.kwargs["uri"] for c in mock_refresh.await_args_list]
            assert "viking://user/alice/memories/identity/identity.md" in refreshed_uris
            assert "viking://user/alice/memories/soul/soul.md" in refreshed_uris
            # memory_type derived from URI
            for c in mock_refresh.await_args_list:
                assert c.kwargs["memory_type"] in {"identity", "soul"}

    @pytest.mark.asyncio
    async def test_no_refresh_when_nothing_created(self):
        vikingdb = MagicMock()
        vikingdb.has_queue_manager = True

        async def _fake_init(_ctx, allowed_memory_types=None):
            return []

        with (
            patch(
                "openviking.session.memory.memory_type_registry.create_default_registry"
            ) as mock_registry_factory,
            patch(
                "openviking.session.memory.memory_updater.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
            ) as mock_refresh,
        ):
            fake_registry = MagicMock()
            fake_registry.initialize_memory_files = _fake_init
            mock_registry_factory.return_value = fake_registry

            created = await MemoryUpdater.initialize_and_vectorize_default_files(
                vikingdb=vikingdb,
                ctx=_ctx(),
            )
            assert created == []
            mock_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_refresh_when_vikingdb_unavailable(self):
        """Without a queue manager (e.g. vikingdb disabled) nothing is enqueued."""
        vikingdb = MagicMock()
        vikingdb.has_queue_manager = False
        with (
            patch(
                "openviking.session.memory.memory_type_registry.create_default_registry"
            ) as mock_registry_factory,
            patch(
                "openviking.session.memory.memory_updater.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
            ) as mock_refresh,
        ):
            created = await MemoryUpdater.initialize_and_vectorize_default_files(
                vikingdb=vikingdb,
                ctx=_ctx(),
            )
            assert created == []
            mock_registry_factory.assert_not_called()
            mock_refresh.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────────────────
# 3. apply_operations: exactly one L1 OVERVIEW per parent, no L0/leaf confusion
# ──────────────────────────────────────────────────────────────────────────────


class TestApplyOperationsOverviewVectorization:
    @staticmethod
    def _make_updater(fs):
        vikingdb = AsyncMock()
        vikingdb.enqueue_embedding_msg.return_value = True
        vikingdb.has_queue_manager = True

        schema = MemoryTypeSchema(
            memory_type="events",
            description="event memory",
            directory="viking://user/{{ user_space }}/memories/events",
            filename_template="{{ name }}.md",
            fields=[MemoryField(name="content", field_type=FieldType.STRING)],
            overview_template="Events overview: {{ items | length }} items",
        )
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types = {"events": schema}

        updater = MemoryUpdater(registry=registry, vikingdb=vikingdb)
        updater._get_viking_fs = MagicMock(return_value=fs)
        return updater, vikingdb

    @pytest.mark.asyncio
    async def test_emits_single_l1_overview_per_parent_and_keeps_leaf_only(self):
        files: dict[str, str] = {}

        class FakeFS:
            async def read_file(self, uri, ctx=None):
                return files.get(uri, "")

            async def write_file(self, uri, data, ctx=None, lease_ref=None):
                files[uri] = data

            async def ls(self, uri, show_all_hidden=False, ctx=None):
                base = uri.rstrip("/")
                return [
                    {"name": k.split("/")[-1], "isDir": False}
                    for k in files
                    if k.startswith(base + "/")
                ]

            async def exists(self, uri, ctx=None):
                return uri in files

        updater, vikingdb = self._make_updater(FakeFS())

        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    old_memory_file_content=None,
                    memory_type="events",
                    uris=["viking://user/alice/memories/events/launch.md"],
                    memory_fields={"content": "launched the thing"},
                ),
            ],
            delete_file_contents=[],
            errors=[],
        )

        with patch(
            "openviking.utils.embedding_utils.vectorize_directory_meta",
            new_callable=AsyncMock,
        ) as mock_vdm:
            result = await updater.apply_operations(operations=operations, ctx=_ctx())

            # Leaf written, .overview.md not added to written_uris
            assert len(result.written_uris) == 1
            assert result.written_uris[0].endswith("launch.md")
            assert not any(u.endswith(".overview.md") for u in result.written_uris)

            # Exactly one L1 OVERVIEW enqueue for the parent directory
            assert mock_vdm.await_count == 1
            kwargs = mock_vdm.await_args.kwargs
            assert kwargs["uri"] == "viking://user/alice/memories/events"
            assert kwargs["include_abstract"] is False
            assert kwargs["include_overview"] is True
            assert kwargs["context_type"] == "memory"

            # The leaf vector went through the normal DETAIL path
            assert vikingdb.enqueue_embedding_msg.await_count == 1
            leaf_msg = vikingdb.enqueue_embedding_msg.await_args.args[0]
            # leaf context is level 2 (DETAIL), is_leaf=True
            assert leaf_msg.context_data["level"] == 2
            assert leaf_msg.context_data["is_leaf"] is True

    @pytest.mark.asyncio
    async def test_no_overview_when_no_upserts(self):
        class FakeFS:
            async def read_file(self, uri, ctx=None):
                return ""

            async def ls(self, uri, show_all_hidden=False, ctx=None):
                return []

        updater, _ = self._make_updater(FakeFS())
        operations = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )
        with patch(
            "openviking.utils.embedding_utils.vectorize_directory_meta",
            new_callable=AsyncMock,
        ) as mock_vdm:
            await updater.apply_operations(operations=operations, ctx=_ctx())
            mock_vdm.assert_not_awaited()
