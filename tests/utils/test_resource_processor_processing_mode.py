# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.context_update_plan import (
    ContextUpdatePlan,
    SemanticPlan,
    SemanticTreeEntry,
    SemanticTreeSnapshot,
)
from openviking.storage.index_action import FieldPatch
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.resource_processor import ResourceProcessor
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.parser_config import FeishuConfig


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
async def test_github_token_prefers_account_config_over_environment(monkeypatch, ctx):
    monkeypatch.setenv("GITHUB_TOKEN", "environment-token")
    monkeypatch.setattr(
        "openviking.utils.resource_processor.is_github_url",
        lambda _source: True,
    )
    manager = SimpleNamespace(
        get_account=AsyncMock(return_value=SimpleNamespace(token="account-token"))
    )
    processor = ResourceProcessor(
        _FakeVikingDB(),
        runtime_config_manager=manager,
    )

    token = await processor.github_token_for("https://github.com/org/private", ctx)

    assert token == "account-token"
    manager.get_account.assert_awaited_once_with("account-1", "github")


@pytest.mark.parametrize(
    "source",
    [
        "git@github.com:org/private.git",
        "ssh://git@github.com/org/private.git",
    ],
)
@pytest.mark.asyncio
async def test_github_token_is_not_injected_into_ssh_sources(monkeypatch, ctx, source):
    monkeypatch.setenv("GITHUB_TOKEN", "environment-token")
    monkeypatch.setattr(
        "openviking.utils.resource_processor.is_github_url",
        lambda _source: True,
    )
    manager = SimpleNamespace(
        get_account=AsyncMock(return_value=SimpleNamespace(token="account-token"))
    )
    processor = ResourceProcessor(
        _FakeVikingDB(),
        runtime_config_manager=manager,
    )
    kwargs = {"branch": "main"}

    assert await processor.github_token_for(source, ctx) is None
    assert await processor._source_config_kwargs(source, ctx, kwargs) is kwargs
    manager.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_source_config_uses_cluster_domain(ctx):
    async def resolve_account(account_id, resolver):
        assert account_id == "account-1"
        return resolver(
            SimpleNamespace(
                account=SimpleNamespace(feishu=None),
                cluster=SimpleNamespace(
                    feishu=FeishuConfig(
                        app_id="current-app",
                        app_secret="current-secret",
                        domain="https://open.feishu.cn",
                        download_images=False,
                    )
                ),
            )
        )

    manager = SimpleNamespace(resolve_account=resolve_account)
    processor = ResourceProcessor(
        _FakeVikingDB(),
        runtime_config_manager=manager,
    )

    kwargs = await processor._source_config_kwargs(
        "https://example.feishu.cn/docx/doc",
        ctx,
        {},
    )

    assert kwargs["feishu_config"].app_id == "current-app"
    assert kwargs["feishu_config"].app_secret == "current-secret"
    assert kwargs["feishu_config"].domain == "https://open.feishu.cn"
    assert kwargs["feishu_config"].download_images is False


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
        file_md5=None,
        file_abstract="",
    )


@pytest.mark.asyncio
async def test_local_flat_file_refresh_passes_final_md5(monkeypatch, ctx):
    root_uri = "viking://resources/report.md"
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    summarizer = SimpleNamespace(refresh_file_parent=AsyncMock(return_value={"status": "success"}))
    processor._get_summarizer = Mock(return_value=summarizer)

    await processor.finish_prepared_resource(
        {
            "root_uri": root_uri,
            "temp_uri": root_uri,
            "source_committed": True,
            "target_preexisting": True,
            "root_is_file": True,
            "file_md5s": {root_uri: "final-md5"},
        },
        ctx=ctx,
        build_index=True,
    )

    assert summarizer.refresh_file_parent.await_args.kwargs["file_md5"] == "final-md5"


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
async def test_local_artifact_without_semantic_work_is_cleaned(monkeypatch, ctx, tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    store = LocalParseOutputStore(local_root=str(tmp_path))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repository/a.py", b"a")
    processor = ResourceProcessor(_FakeVikingDB())
    processor._build_parse_output_store = Mock(return_value=store)

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://resources/demo",
            "source_committed": True,
            "artifact_ref": ref.to_dict(),
            "artifact_files": ["a.py"],
        },
        ctx=ctx,
        build_index=False,
    )

    assert not tmp_path.joinpath(ref.root).exists()


@pytest.mark.asyncio
async def test_local_incremental_noop_skips_semantic_queue_and_releases_resources(
    monkeypatch, ctx, tmp_path
):
    from openviking.parse.output import LocalParseOutputStore

    store = LocalParseOutputStore(local_root=str(tmp_path))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repository/a.py", b"a")
    lock = {"lease_ref": "noop"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._build_parse_output_store = Mock(return_value=store)
    summarizer = SimpleNamespace(summarize=AsyncMock(return_value={"status": "success"}))
    processor._get_summarizer = Mock(return_value=summarizer)

    result = await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://resources/demo",
            "source_committed": True,
            "target_preexisting": True,
            "artifact_ref": ref.to_dict(),
            "artifact_files": ["a.py"],
            "changes": {},
            "file_md5s": {},
            "file_abstracts": {},
            "incremental_noop": True,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
    )

    assert result == {"status": "success", "root_uri": "viking://resources/demo"}
    summarizer.summarize.assert_not_awaited()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)
    assert not tmp_path.joinpath(ref.root).exists()


@pytest.mark.asyncio
async def test_vectors_only_scalar_update_is_enqueued_before_lock_release(monkeypatch, ctx):
    from openviking.storage.context_update_plan import DirectIndexAction

    queue = SimpleNamespace(enqueue=AsyncMock(return_value="queued"))
    manager = SimpleNamespace(
        EMBEDDING="embedding",
        get_queue=lambda *_args, **_kwargs: queue,
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    action = DirectIndexAction(
        action="update_fields",
        record_id="a-l2",
        uri="viking://resources/demo/a.py",
        level=2,
        field_patch=FieldPatch({"search_tags": ["team=search"]}),
    )
    plan = ContextUpdatePlan(
        root_uri="viking://resources/demo",
        context_type="resource",
        direct_index_actions=(action,),
    )
    lock = {"lease_ref": "tags"}

    result = await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://resources/demo",
            "source_committed": True,
            "target_preexisting": True,
            "incremental_noop": True,
            "context_update_plan": plan.to_dict(),
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=False,
        processing_mode="vectors_only",
    )

    assert result == {"status": "success", "root_uri": "viking://resources/demo"}
    message = queue.enqueue.await_args.args[0]
    assert message.action.value == "update_fields"
    assert message.record_ids == ["a-l2"]
    assert message.update_fields["search_tags"] == ["team=search"]
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_directory_semantic_plan_is_enqueued_without_artifact_or_legacy_diff(
    monkeypatch, ctx
):
    root_uri = "viking://resources/demo"
    plan = SemanticPlan(
        root_uri=root_uri,
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "unchanged", "aggregate"),
                SemanticTreeEntry("a.py", "file", "modified", "generate", md5="new"),
            )
        ),
    )
    context_plan = ContextUpdatePlan(root_uri, "resource", semantic_plan=plan)
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_to_handoff=AsyncMock(return_value={"lease": "x"}),
            pathlock_handoff=AsyncMock(),
            pathlock_release=AsyncMock(),
        )
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    summarizer = SimpleNamespace(
        summarize=AsyncMock(return_value={"status": "success", "enqueued_count": 1})
    )
    processor._get_summarizer = Mock(return_value=summarizer)

    await processor.finish_prepared_resource(
        {
            "root_uri": root_uri,
            "temp_uri": root_uri,
            "source_committed": True,
            "target_preexisting": True,
            "root_is_file": False,
            "context_update_plan": context_plan.to_dict(),
        },
        ctx=ctx,
        resource_lock={"lease_ref": "plan"},
        build_index=True,
        processing_mode="semantic_and_vectors",
    )

    kwargs = summarizer.summarize.await_args.kwargs
    assert SemanticPlan.from_dict(kwargs["semantic_plan"]) == plan
    assert kwargs["temp_uris"] == [root_uri]
    assert kwargs.get("changes") is None
    assert kwargs.get("artifact_ref") is None


@pytest.mark.asyncio
async def test_plan_artifact_is_cleaned_only_after_semantic_enqueue(monkeypatch, ctx, tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    root_uri = "viking://resources/demo"
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repository/a.py", b"a")
    plan = SemanticPlan(
        root_uri=root_uri,
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "added", "aggregate"),
                SemanticTreeEntry("a.py", "file", "added", "generate", md5="a"),
            )
        ),
    )
    context_plan = ContextUpdatePlan(root_uri, "resource", semantic_plan=plan)
    lock = {"lease_ref": "plan"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_handoff=AsyncMock(),
            pathlock_release=AsyncMock(),
        )
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._build_parse_output_store = Mock(return_value=store)

    async def summarize(**kwargs):
        assert kwargs.get("artifact_ref") is None
        assert tmp_path.joinpath("artifacts", ref.root.split("/")[-1]).exists()
        return {"status": "success", "enqueued_count": 1}

    processor._get_summarizer = Mock(
        return_value=SimpleNamespace(summarize=AsyncMock(side_effect=summarize))
    )

    await processor.finish_prepared_resource(
        {
            "root_uri": root_uri,
            "temp_uri": root_uri,
            "source_committed": True,
            "target_preexisting": True,
            "root_is_file": False,
            "artifact_ref": ref.to_dict(),
            "context_update_plan": context_plan.to_dict(),
            "plan_artifact_committed": True,
        },
        ctx=ctx,
        resource_lock=lock,
        build_index=True,
    )

    assert not tmp_path.joinpath("artifacts", ref.root.split("/")[-1]).exists()
    viking_fs._async_agfs.pathlock_handoff.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_plan_enqueue_failure_cleans_artifact_and_releases_lock(monkeypatch, ctx, tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    root_uri = "viking://resources/demo"
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repository/a.py", b"a")
    plan = SemanticPlan(
        root_uri=root_uri,
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "added", "aggregate"),
                SemanticTreeEntry("a.py", "file", "added", "generate", md5="a"),
            )
        ),
    )
    context_plan = ContextUpdatePlan(root_uri, "resource", semantic_plan=plan)
    lock = {"lease_ref": "plan"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_handoff=AsyncMock(),
            pathlock_release=AsyncMock(),
        )
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._build_parse_output_store = Mock(return_value=store)
    processor._get_summarizer = Mock(
        return_value=SimpleNamespace(
            summarize=AsyncMock(side_effect=RuntimeError("queue unavailable"))
        )
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await processor.finish_prepared_resource(
            {
                "root_uri": root_uri,
                "temp_uri": root_uri,
                "source_committed": True,
                "target_preexisting": True,
                "root_is_file": False,
                "artifact_ref": ref.to_dict(),
                "context_update_plan": context_plan.to_dict(),
                "plan_artifact_committed": True,
            },
            ctx=ctx,
            resource_lock=lock,
            build_index=True,
        )

    assert not tmp_path.joinpath("artifacts", ref.root.split("/")[-1]).exists()
    viking_fs._async_agfs.pathlock_handoff.assert_not_awaited()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_plan_enqueue_error_result_cleans_artifact_and_fails(monkeypatch, ctx, tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    root_uri = "viking://resources/demo"
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repository/a.py", b"a")
    plan = SemanticPlan(
        root_uri=root_uri,
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "added", "aggregate"),
                SemanticTreeEntry("a.py", "file", "added", "generate", md5="a"),
            )
        ),
    )
    context_plan = ContextUpdatePlan(root_uri, "resource", semantic_plan=plan)
    lock = {"lease_ref": "plan"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_handoff=AsyncMock(),
            pathlock_release=AsyncMock(),
        )
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(_FakeVikingDB())
    processor._build_parse_output_store = Mock(return_value=store)
    processor._get_summarizer = Mock(
        return_value=SimpleNamespace(
            summarize=AsyncMock(return_value={"status": "error", "message": "queue rejected plan"})
        )
    )

    with pytest.raises(RuntimeError, match="queue rejected plan"):
        await processor.finish_prepared_resource(
            {
                "root_uri": root_uri,
                "temp_uri": root_uri,
                "source_committed": True,
                "target_preexisting": True,
                "root_is_file": False,
                "artifact_ref": ref.to_dict(),
                "context_update_plan": context_plan.to_dict(),
                "plan_artifact_committed": True,
            },
            ctx=ctx,
            resource_lock=lock,
            build_index=True,
        )

    assert not tmp_path.joinpath("artifacts", ref.root.split("/")[-1]).exists()
    viking_fs._async_agfs.pathlock_handoff.assert_not_awaited()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_local_flat_refresh_failure_cleans_artifact(monkeypatch, ctx, tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    store = LocalParseOutputStore(local_root=str(tmp_path))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "document/report.md", b"body")
    processor = ResourceProcessor(_FakeVikingDB())
    processor._build_parse_output_store = Mock(return_value=store)
    processor._get_summarizer = Mock(
        return_value=SimpleNamespace(
            refresh_file_parent=AsyncMock(side_effect=RuntimeError("queue unavailable"))
        )
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await processor.finish_prepared_resource(
            {
                "root_uri": "viking://resources/report.md",
                "temp_uri": "viking://resources/report.md",
                "source_committed": True,
                "target_preexisting": True,
                "root_is_file": True,
                "artifact_ref": ref.to_dict(),
                "artifact_files": [""],
            },
            ctx=ctx,
            build_index=True,
        )

    assert not tmp_path.joinpath(ref.root).exists()


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


@pytest.mark.asyncio
async def test_local_vectors_only_uses_artifact_snapshot_when_target_tree_is_empty(
    monkeypatch, ctx, tmp_path
):
    from openviking.parse.output import LocalParseOutputStore, ParseArtifactRef
    from openviking.utils.content_hash import content_md5

    store = LocalParseOutputStore(local_root=str(tmp_path))
    raw_ref = await store.create_artifact()
    ref = ParseArtifactRef(
        backend="local",
        root=raw_ref.root,
        resource_rel="repository",
    )
    await store.write_bytes(ref, "repository/a.py", b"print('a')")
    viking_fs = SimpleNamespace(tree=AsyncMock(return_value=[]))
    vectorize_file = AsyncMock(return_value=True)
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
    processor._build_parse_output_store = Mock(return_value=store)

    await processor.finish_prepared_resource(
        {
            "root_uri": "viking://resources/demo",
            "temp_uri": "viking://resources/demo",
            "source_committed": True,
            "artifact_ref": ref.to_dict(),
            "artifact_files": ["a.py"],
            "file_md5s": {"viking://resources/demo/a.py": content_md5(b"print('a')")},
        },
        ctx=ctx,
        build_index=True,
        processing_mode="vectors_only",
    )

    viking_fs.tree.assert_not_awaited()
    vectorize_file.assert_awaited_once()
    assert vectorize_file.await_args.kwargs["file_content"] == b"print('a')"
    assert vectorize_file.await_args.kwargs["file_md5"] == content_md5(b"print('a')")


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
