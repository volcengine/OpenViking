# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils.resource_processor import ResourceProcessor
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingDB:
    def get_embedder(self):
        return None


class _RecordingVikingDB:
    def __init__(self):
        self.lookup_calls = []
        self.filter_calls = []
        self.delete_calls = []

    def get_embedder(self):
        return None

    async def get_context_by_uri(self, *, uri, level, limit, ctx):
        self.lookup_calls.append((uri, level, limit, ctx))
        return [{"id": f"{uri}:{level}"}]

    async def delete(self, ids, *, ctx):
        self.delete_calls.append((ids, ctx))
        return len(ids)

    async def filter(self, *, filter, limit, output_fields, ctx):
        self.filter_calls.append((filter, limit, output_fields, ctx))
        return [{"id": "recursive-child-detail"}]


@pytest.fixture
def ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_vectors_only_persists_tree_and_vectorizes_files_only(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        persist_temp_tree=AsyncMock(),
        delete_temp=AsyncMock(),
        tree=AsyncMock(
            return_value=[
                {"uri": "viking://resources/demo/section", "isDir": True},
                {
                    "uri": "viking://resources/demo/section/page.md",
                    "isDir": False,
                    "name": "page.md",
                },
                {
                    "uri": "viking://resources/demo/section/.abstract.md",
                    "isDir": False,
                    "name": ".abstract.md",
                },
            ]
        ),
    )
    vectorize_file = AsyncMock()
    rewrite_image_uris = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.utils.resource_processor.rewrite_image_uris", rewrite_image_uris)
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", vectorize_file)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._get_summarizer = Mock(side_effect=AssertionError("summarizer should not run"))
    processor._delete_resource_semantic_markers = AsyncMock()
    processor._delete_resource_semantic_vectors = AsyncMock()
    processor._delete_removed_resource_vectors = AsyncMock()
    lock = SimpleNamespace(active=True, handle="lock-1", close=AsyncMock())

    result = await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://temp/demo",
            "temp_dir_path": "tmp/demo",
            "source_committed": False,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
        processing_mode="vectors_only",
    )

    assert result == {"status": "success", "root_uri": "viking://resources/demo"}
    viking_fs.persist_temp_tree.assert_awaited_once_with(
        "viking://temp/demo", "viking://resources/demo", ctx=ctx
    )
    rewrite_image_uris.assert_awaited_once_with(
        "viking://resources/demo",
        ctx=ctx,
        lock_handle="lock-1",
    )
    viking_fs.delete_temp.assert_awaited_once_with("tmp/demo", ctx=ctx)
    vectorize_file.assert_awaited_once()
    assert vectorize_file.await_args.kwargs["file_path"] == (
        "viking://resources/demo/section/page.md"
    )
    assert vectorize_file.await_args.kwargs["parent_uri"] == "viking://resources/demo/section"
    assert vectorize_file.await_args.kwargs["summary_dict"] == {
        "name": "page.md",
        "summary": "",
    }
    assert vectorize_file.await_args.kwargs["register_request_wait"] is True
    processor._delete_resource_semantic_markers.assert_not_awaited()
    processor._delete_resource_semantic_vectors.assert_not_awaited()
    lock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_vectors_only_skips_vectorization_when_build_index_false(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        persist_temp_tree=AsyncMock(),
        delete_temp=AsyncMock(),
        tree=AsyncMock(return_value=[]),
    )
    vectorize_file = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.utils.resource_processor.rewrite_image_uris", AsyncMock())
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", vectorize_file)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_markers = AsyncMock()
    processor._delete_resource_semantic_vectors = AsyncMock()
    lock = SimpleNamespace(active=True, handle="lock-1", close=AsyncMock())

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://temp/demo",
            "source_committed": False,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=False,
        processing_mode="vectors_only",
    )

    processor._delete_resource_semantic_markers.assert_not_awaited()
    processor._delete_resource_semantic_vectors.assert_not_awaited()
    vectorize_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_vectors_only_syncs_preexisting_target_instead_of_merging(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        persist_temp_tree=AsyncMock(),
        delete_temp=AsyncMock(),
        tree=AsyncMock(return_value=[]),
    )
    sync = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.utils.resource_processor.SemanticProcessor", Mock())
    monkeypatch.setattr(
        "openviking.utils.resource_processor.SemanticProcessor.return_value._sync_topdown_recursive",
        sync,
    )
    monkeypatch.setattr("openviking.utils.resource_processor.rewrite_image_uris", AsyncMock())
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", AsyncMock())
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_vectors = AsyncMock()
    lock = SimpleNamespace(active=True, handle="lock-1", close=AsyncMock())

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://temp/demo",
            "temp_dir_path": "viking://temp/demo",
            "source_committed": False,
            "target_preexisting": True,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
        processing_mode="vectors_only",
    )

    viking_fs.persist_temp_tree.assert_not_awaited()
    sync.assert_awaited_once_with(
        "viking://temp/demo",
        "viking://resources/demo",
        ctx=ctx,
        lock=lock,
    )


@pytest.mark.asyncio
async def test_vectors_only_leaves_existing_semantic_vectors_untouched(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        persist_temp_tree=AsyncMock(),
        delete_temp=AsyncMock(),
        tree=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.utils.resource_processor.rewrite_image_uris", AsyncMock())
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", AsyncMock())
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_vectors = AsyncMock()
    processor._delete_removed_resource_vectors = AsyncMock()
    lock = SimpleNamespace(active=True, handle="lock-1", close=AsyncMock())

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://temp/demo",
            "source_committed": False,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
        processing_mode="vectors_only",
    )

    processor._delete_resource_semantic_vectors.assert_not_awaited()


@pytest.mark.asyncio
async def test_vectors_only_deletes_sync_removed_detail_vectors(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        persist_temp_tree=AsyncMock(),
        delete_temp=AsyncMock(),
        tree=AsyncMock(return_value=[]),
    )
    diff = SimpleNamespace(
        deleted_files=["viking://resources/demo/old.md"],
        deleted_dirs=["viking://resources/demo/old-dir"],
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.utils.resource_processor.SemanticProcessor", Mock())
    monkeypatch.setattr(
        "openviking.utils.resource_processor.SemanticProcessor.return_value._sync_topdown_recursive",
        AsyncMock(return_value=diff),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.rewrite_image_uris", AsyncMock())
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", AsyncMock())
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_vectors = AsyncMock()
    processor._delete_removed_resource_vectors = AsyncMock()
    lock = SimpleNamespace(active=True, handle="lock-1", close=AsyncMock())

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://temp/demo",
            "source_committed": False,
            "target_preexisting": True,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
        processing_mode="vectors_only",
    )

    processor._delete_removed_resource_vectors.assert_awaited_once_with(
        files=["viking://resources/demo/old.md"],
        dirs=["viking://resources/demo/old-dir"],
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_delete_removed_resource_vectors_deletes_detail_records(ctx):
    vikingdb = _RecordingVikingDB()
    processor = ResourceProcessor(vikingdb)

    await processor._delete_removed_resource_vectors(
        files=["viking://resources/demo/old.md"],
        dirs=["viking://resources/demo/old-dir"],
        ctx=ctx,
    )

    assert vikingdb.lookup_calls == [
        ("viking://resources/demo/old.md", 2, 100, ctx),
    ]
    assert len(vikingdb.filter_calls) == 1
    assert vikingdb.filter_calls[0][1] == 100_000
    assert vikingdb.delete_calls == [
        (["viking://resources/demo/old.md:2"], ctx),
        (["recursive-child-detail"], ctx),
    ]
