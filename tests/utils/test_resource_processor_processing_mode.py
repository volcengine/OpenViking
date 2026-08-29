# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils.ingest_options import IngestOptions
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


@pytest.mark.asyncio
async def test_resource_processor_upload_understanding_file_delegates():
    processor = ResourceProcessor(_FakeVikingDB())
    media_processor = SimpleNamespace(upload_understanding_file=AsyncMock(return_value="file-1"))
    processor._media_processor = media_processor

    result = await processor.upload_understanding_file("/tmp/upload.pdf")

    assert result == "file-1"
    media_processor.upload_understanding_file.assert_awaited_once_with("/tmp/upload.pdf")


@pytest.fixture
def ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_flat_file_refreshes_parent_semantics_and_vectorizes_via_summary(
    monkeypatch,
    ctx,
):
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
        tree=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_viking_fs",
        lambda: viking_fs,
    )
    processor = ResourceProcessor(_FakeVikingDB())
    summarizer = SimpleNamespace(refresh_file_parent=AsyncMock(return_value={"status": "success"}))
    processor._get_summarizer = Mock(return_value=summarizer)

    result = await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/神雕_副本.md",
            "temp_uri": "viking://resources/神雕_副本.md",
            "source_committed": True,
            "root_is_file": True,
        },
        ctx=ctx,
        resource_lock={"lease_ref": "flat-file"},
        build_index=True,
        processing_mode="semantic_and_vectors",
    )

    assert result == {
        "status": "success",
        "root_uri": "viking://resources/神雕_副本.md",
    }
    summarizer.refresh_file_parent.assert_awaited_once_with(
        file_uri="viking://resources/神雕_副本.md",
        ctx=ctx,
        skip_vectorization=False,
        ingest_options=IngestOptions(),
        created=True,
    )


@pytest.mark.asyncio
async def test_flat_file_skips_all_post_processing_when_build_index_false(
    monkeypatch,
    ctx,
):
    vectorize_file = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", vectorize_file)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._get_summarizer = Mock(
        side_effect=AssertionError("flat files have no directory semantics")
    )

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/神雕_副本.md",
            "temp_uri": "viking://resources/神雕_副本.md",
            "source_committed": True,
            "root_is_file": True,
        },
        ctx=ctx,
        build_index=False,
    )

    vectorize_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_vectors_only_replaces_preexisting_flat_file_without_directory_sync(
    monkeypatch,
    ctx,
):
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
        exists=AsyncMock(return_value=True),
        ls=AsyncMock(side_effect=NotADirectoryError("flat resource roots cannot be listed")),
        persist_temp_tree=AsyncMock(),
        delete_temp=AsyncMock(),
    )
    vectorize_file = AsyncMock()
    rewrite_image_uris = AsyncMock()
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_viking_fs",
        lambda: viking_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: viking_fs,
    )
    monkeypatch.setattr(
        "openviking.utils.resource_processor.rewrite_image_uris",
        rewrite_image_uris,
    )
    monkeypatch.setattr(
        "openviking.utils.resource_processor.vectorize_file",
        vectorize_file,
    )
    processor = ResourceProcessor(_FakeVikingDB())
    processor._get_summarizer = Mock(
        side_effect=AssertionError("flat files have no directory semantics")
    )
    lock = {"lease_ref": "flat-file"}

    result = await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/神雕_副本.md",
            "temp_uri": "viking://temp/神雕_副本.md",
            "temp_dir_path": "viking://temp/job-1",
            "source_committed": False,
            "target_preexisting": True,
            "root_is_file": True,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
        processing_mode="vectors_only",
    )

    assert result == {
        "status": "success",
        "root_uri": "viking://resources/神雕_副本.md",
    }
    viking_fs.persist_temp_tree.assert_awaited_once_with(
        "viking://temp/神雕_副本.md",
        "viking://resources/神雕_副本.md",
        ctx=ctx,
        lease_ref=lock,
    )
    viking_fs.delete_temp.assert_awaited_once_with("viking://temp/job-1", ctx=ctx)
    rewrite_image_uris.assert_not_awaited()
    vectorize_file.assert_awaited_once()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_vectors_only_persists_tree_and_vectorizes_files_only(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
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
                    "uri": "viking://resources/demo/section/notes.txt",
                    "isDir": False,
                    "name": "notes.txt",
                },
                {
                    "uri": "viking://resources/demo/section/.abstract.md",
                    "isDir": False,
                    "name": ".abstract.md",
                },
            ]
        ),
    )
    vectorized = {}
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def vectorize_file(**kwargs):
        vectorized[kwargs["file_path"]] = kwargs
        if len(vectorized) == 2:
            both_entered.set()
        await release.wait()

    rewrite_image_uris = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr(
        "openviking.utils.resource_processor.rewrite_image_uris", rewrite_image_uris
    )
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", vectorize_file)
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_vectorization_concurrency=8)
            )
        ),
    )
    processor = ResourceProcessor(_FakeVikingDB())
    processor._get_summarizer = Mock(side_effect=AssertionError("summarizer should not run"))
    processor._delete_resource_semantic_markers = AsyncMock()
    processor._delete_resource_semantic_vectors = AsyncMock()
    processor._delete_removed_resource_vectors = AsyncMock()
    lock = {"lease_ref": "lock-1"}

    task = asyncio.create_task(
        processor.finish_prepared_resource(
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
            ingest_options=IngestOptions.from_search_tags(["team=search"], mode="append"),
        )
    )
    try:
        await asyncio.wait_for(both_entered.wait(), timeout=1)
    finally:
        release.set()
        await task
    result = task.result()

    assert result == {"status": "success", "root_uri": "viking://resources/demo"}
    viking_fs.persist_temp_tree.assert_awaited_once_with(
        "viking://temp/demo",
        "viking://resources/demo",
        ctx=ctx,
        lease_ref=lock,
    )
    rewrite_image_uris.assert_awaited_once_with(
        "viking://resources/demo",
        ctx=ctx,
        lease_ref=lock,
    )
    viking_fs.delete_temp.assert_awaited_once_with("tmp/demo", ctx=ctx)
    assert set(vectorized) == {
        "viking://resources/demo/section/page.md",
        "viking://resources/demo/section/notes.txt",
    }
    page = vectorized["viking://resources/demo/section/page.md"]
    assert page["parent_uri"] == "viking://resources/demo/section"
    assert page["summary_dict"] == {
        "name": "page.md",
        "summary": "",
    }
    assert page["ingest_options"] == IngestOptions(
        search_tags=["team=search"],
        search_tag_mode="append",
    )
    processor._delete_resource_semantic_markers.assert_not_awaited()
    processor._delete_resource_semantic_vectors.assert_not_awaited()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_vectors_only_skips_vectorization_when_build_index_false(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
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
    lock = {"lease_ref": "lock-1"}

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
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
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
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_vectorization_concurrency=8)
            )
        ),
    )
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_vectors = AsyncMock()
    lock = {"lease_ref": "lock-1"}

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
async def test_vectors_only_cancels_siblings_before_releasing_lock(monkeypatch, ctx):
    healthy_started = asyncio.Event()
    healthy_cancelled = asyncio.Event()
    lock = {"lease_ref": "lock-1"}

    async def release_lock(released_lock):
        assert released_lock == lock
        assert healthy_cancelled.is_set()

    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock(side_effect=release_lock)),
        tree=AsyncMock(
            return_value=[
                {
                    "uri": "viking://resources/demo/failing.md",
                    "isDir": False,
                    "name": "failing.md",
                },
                {
                    "uri": "viking://resources/demo/healthy.md",
                    "isDir": False,
                    "name": "healthy.md",
                },
            ]
        ),
    )

    async def vectorize_file(*, file_path, **kwargs):
        if file_path.endswith("failing.md"):
            await healthy_started.wait()
            raise RuntimeError("vector enqueue failed")
        healthy_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            healthy_cancelled.set()
            raise

    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.utils.resource_processor.vectorize_file", vectorize_file)
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_vectorization_concurrency=8)
            )
        ),
    )
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_vectors = AsyncMock()

    with pytest.raises(RuntimeError, match="vector enqueue failed"):
        await processor.finish_prepared_resource(
            {
                "root_uri": "viking://resources/demo",
                "source_committed": True,
            },
            ctx=ctx,
            resource_lock=lock,
            build_index=True,
            processing_mode="vectors_only",
        )

    assert healthy_cancelled.is_set()
    processor._delete_resource_semantic_vectors.assert_not_awaited()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_vectors_only_deletes_sync_removed_detail_vectors(monkeypatch, ctx):
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
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
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_vectorization_concurrency=8)
            )
        ),
    )
    processor = ResourceProcessor(_FakeVikingDB())
    processor._delete_resource_semantic_vectors = AsyncMock()
    processor._delete_removed_resource_vectors = AsyncMock()
    lock = {"lease_ref": "lock-1"}

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
