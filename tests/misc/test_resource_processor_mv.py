import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.context_update_plan import (
    ContextUpdatePlan,
    FileRefreshIntent,
    SemanticPlan,
    SemanticTreeEntry,
    SemanticTreeSnapshot,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class _DummyVikingDB:
    def get_embedder(self):
        return None


def _directory_plan(root_uri: str) -> ContextUpdatePlan:
    return ContextUpdatePlan(
        root_uri,
        "resource",
        semantic_plan=SemanticPlan(
            root_uri,
            "resource",
            SemanticTreeSnapshot((SemanticTreeEntry("", "directory", "added", "aggregate"),)),
        ),
    )


class _DummyTelemetry:
    def set(self, *args, **kwargs):
        return None

    def set_error(self, *args, **kwargs):
        return None

    class _Measure:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def measure(self, *args, **kwargs):
        return self._Measure()


class _CtxMgr:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePathLock:
    def __init__(self, *, busy_tree_paths=None):
        self._next_id = 0
        self.acquired_exact_paths: list[str] = []
        self.acquired_tree_paths: list[str] = []
        self.exact_attempts: list[tuple[str, float]] = []
        self.tree_attempts: list[tuple[str, float]] = []
        self.busy_tree_paths = set(busy_tree_paths or [])

    def _new_lease(self):
        self._next_id += 1
        return {"id": f"lock-{self._next_id}"}

    async def pathlock_acquire_tree(self, path, timeout_secs=0.0):
        from openviking.storage.errors import LockAcquisitionError

        self.tree_attempts.append((path, timeout_secs))
        if path in self.busy_tree_paths:
            raise LockAcquisitionError(f"busy: {path}")
        self.acquired_tree_paths.append(path)
        return self._new_lease()

    async def pathlock_acquire_exact(self, path, timeout_secs=0.0):
        self.exact_attempts.append((path, timeout_secs))
        self.acquired_exact_paths.append(path)
        return self._new_lease()

    async def pathlock_release(self, lease):
        pass

    async def pathlock_handoff(self, lease):
        pass


class _FakeVikingFS:
    def __init__(self, *, exists_result=False, existing_uris=None, pathlock=None):
        self.agfs = SimpleNamespace(
            write=MagicMock(return_value={"status": "ok"}),
        )
        self._async_agfs = pathlock or _FakePathLock()
        self._exists_result = exists_result
        self._existing_uris = set(existing_uris or [])
        self.exists_calls = []
        self.persist_calls = []
        self.delete_temp_calls = []

    def bind_request_context(self, ctx):
        return _CtxMgr()

    async def _ensure_access(self, uri, ctx, action):
        return None

    async def exists(self, uri, ctx=None):
        self.exists_calls.append(uri)
        if self._existing_uris:
            return uri in self._existing_uris
        return self._exists_result

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        return None

    async def delete_temp(self, temp_dir_path, ctx=None, lease_ref=None):
        self.delete_temp_calls.append((temp_dir_path, lease_ref))
        return None

    async def persist_temp_tree(self, temp_uri, target_uri, ctx=None, lease_ref=None):
        self.persist_calls.append((temp_uri, target_uri, lease_ref))
        self.agfs.write(self._uri_to_path(target_uri, ctx=ctx), b"content")

    async def glob(self, pattern, uri=None, ctx=None):
        return {"matches": []}

    def _uri_to_path(self, uri, ctx=None):
        return f"/mock/{uri.replace('viking://', '')}"


def _patch_viking_fs(monkeypatch, fake_fs):
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr("openviking.parse.image_rewrite.get_viking_fs", lambda: fake_fs)


@pytest.mark.asyncio
async def test_resource_processor_rejects_directory_when_every_file_failed(monkeypatch):
    from openviking.server.responses import response_from_result
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    failed_files = [
        {
            "path": "native.pdf",
            "parser": "PDFParser",
            "error": "native parser rejected file",
        },
        {
            "path": "remote.pdf",
            "parser": "UnderstandingAPI",
            "file_id": "file-1",
            "response_id": "response-1",
            "error": "remote parser rejected file",
        },
    ]
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/empty-directory",
            source_path="directory.zip",
            # Local directories arrive through temp-upload as ZIP files. ZipParser
            # preserves DirectoryParser's aggregate metadata while changing this
            # outer format marker to "zip".
            source_format="zip",
            meta={
                "file_count": 0,
                "total_processable": 2,
                "processed_files": [],
                "failed_files": failed_files,
            },
            warnings=[],
        )
    )
    rp.tree_builder.finalize_from_temp = AsyncMock()

    result = await rp.process_resource(path="directory.zip", ctx=object(), build_index=True)

    assert result["status"] == "error"
    assert result["meta"]["failed_files"] == failed_files
    assert "all 2 processable file(s) failed" in result["errors"][0]
    assert "native.pdf: native parser rejected file" in result["errors"][0]
    assert (
        "remote.pdf (file_id=file-1, response_id=response-1): remote parser rejected file"
        in result["errors"][0]
    )
    response = response_from_result(result)
    assert response.status_code == 500
    assert json.loads(response.body)["error"]["code"] == "PROCESSING_ERROR"
    assert fake_fs.delete_temp_calls == [("viking://temp/empty-directory", None)]
    assert fake_fs.persist_calls == []
    rp.tree_builder.finalize_from_temp.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("build_index", [True, False])
async def test_resource_processor_rejects_partial_directory_before_semantic_plan_diff(
    monkeypatch, build_index
):
    from openviking.parse.output import ParseArtifactRef
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS(exists_result=True)
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    ref = ParseArtifactRef(backend="agfs", root="viking://temp/partial")
    parse_result = SimpleNamespace(
        temp_dir_path=ref.root,
        source_path="repo",
        source_format="repository",
        meta={
            "file_count": 1,
            "total_processable": 2,
            "processed_files": [{"path": "kept.py"}],
            "failed_files": [
                {
                    "path": "missing.py",
                    "parser": "native",
                    "error": "parser failed",
                }
            ],
        },
        warnings=[],
        artifact_ref=ref,
        ensure_artifact_ref=lambda: ref,
    )
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(return_value=parse_result)
    rp.tree_builder.finalize_from_temp = AsyncMock(
        side_effect=AssertionError("partial artifact must not reach finalize/diff")
    )
    rp._commit_directory_artifact_with_plan = AsyncMock(
        return_value=_directory_plan("viking://resources/root")
    )

    result = await rp.process_resource(path="repo", ctx=object(), build_index=build_index)

    assert result["status"] == "error"
    assert result["errors"] == [
        "Directory import incomplete: 1 file(s) failed to parse; "
        "failed files: missing.py: parser failed"
    ]
    assert fake_fs.delete_temp_calls == [(ref.root, None)]
    rp.tree_builder.finalize_from_temp.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_directory_rejection_cleans_local_parse_artifact(monkeypatch, tmp_path):
    from openviking.parse.output import LocalParseOutputStore
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS(exists_result=True)
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repo/kept.py", b"pass")
    parse_result = SimpleNamespace(
        temp_dir_path=ref.root,
        source_path="repo",
        source_format="repository",
        meta={
            "file_count": 1,
            "total_processable": 2,
            "processed_files": [{"path": "kept.py"}],
            "failed_files": [{"path": "missing.py", "error": "failed"}],
        },
        warnings=[],
        artifact_ref=ref,
        ensure_artifact_ref=lambda: ref,
    )
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._build_parse_output_store = MagicMock(return_value=store)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(return_value=parse_result)
    rp.tree_builder.finalize_from_temp = AsyncMock()

    result = await rp.process_resource(path="repo", ctx=object(), build_index=True)

    assert result["status"] == "error"
    assert not tmp_path.joinpath("artifacts", ref.root.split("/")[-1]).exists()
    assert fake_fs.delete_temp_calls == []
    rp.tree_builder.finalize_from_temp.assert_not_awaited()


def test_directory_parse_failures_include_failed_source_access_items():
    from openviking.utils.resource_processor import ResourceProcessor

    failures = ResourceProcessor._directory_parse_failures(
        {
            "failed_files": [
                {"path": "broken.pdf", "error": "invalid PDF"},
                {"path": "denied.docx", "status": "failed", "reason": "HTTP 403"},
                {"path": "binary.bin", "status": "unsupported", "reason": "binary"},
                {
                    "path": "unsupported.csv",
                    "status": "unsupported",
                    "error": "unsupported type",
                },
                {"path": "ignored.tmp", "status": "skipped", "error": "ignored"},
            ]
        }
    )

    assert failures == [
        {"path": "broken.pdf", "error": "invalid PDF"},
        {"path": "denied.docx", "status": "failed", "reason": "HTTP 403"},
    ]


@pytest.mark.parametrize("length", [119, 120, 121, 240])
def test_empty_directory_error_keeps_120_character_limit(length):
    from openviking.utils.resource_processor import ResourceProcessor

    reason = "错误原因" * (length // 4) + "错" * (length % 4)
    meta = {
        "total_processable": 1,
        "failed_files": [
            {
                "path": "空白.md",
                "error": reason,
                "file_id": "file-1",
                "response_id": "response-1",
            }
        ],
    }
    message = ResourceProcessor._empty_directory_error(meta)
    assert message.endswith(
        f"failed files: 空白.md (file_id=file-1, response_id=response-1): {reason[:120]}"
    )
    assert message.count("response_id=response-1") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [None, {}, {"file_id": "file-1"}, {"response_id": "response-1"}],
    ids=["native", "no_ids", "file_id_only", "response_id"],
)
@pytest.mark.parametrize(
    "reason", ["文件解析任务失败：empty parse result", "错误原因" * 50], ids=["short", "long"]
)
async def test_resource_processor_preserves_single_file_reason_and_response_id(
    monkeypatch, meta, reason
):
    from openviking.parse.understanding_api import UnderstandingAPIError
    from openviking.server.responses import response_from_result
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)
    error = RuntimeError(reason) if meta is None else UnderstandingAPIError(reason, meta)
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(side_effect=error)
    rp.tree_builder.finalize_from_temp = AsyncMock()

    result = await rp.process_resource(path="report.pdf", ctx=object(), build_index=False)

    expected = f"Parse error: {reason}"
    if meta and meta.get("response_id"):
        expected += " (response_id=response-1)"
    assert result["status"] == "error"
    assert result["errors"] == [expected]
    response = response_from_result(result)
    assert response.status_code == 500
    assert json.loads(response.body)["error"] == {
        "code": "PROCESSING_ERROR",
        "message": expected,
    }
    assert fake_fs.persist_calls == []
    rp.tree_builder.finalize_from_temp.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_processor_first_add_summarizes_from_committed_uri(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    summarize_calls = []

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/tmpdir",
            source_path="x",
            source_format="text",
            meta={},
            warnings=[],
        )
    )
    rp.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(uri="viking://resources/root", temp_uri="viking://temp/root_tmp")
        )
    )
    rp._commit_directory_artifact_with_plan = AsyncMock(
        return_value=_directory_plan("viking://resources/root")
    )
    rp._summarizer = SimpleNamespace(
        summarize=AsyncMock(
            side_effect=lambda *args, **kwargs: (
                summarize_calls.append(kwargs) or {"status": "success"}
            )
        )
    )

    result = await rp.process_resource(path="x", ctx=object(), build_index=True)

    assert result["status"] == "success"
    assert result["root_uri"] == "viking://resources/root"
    assert fake_fs.persist_calls == []
    assert fake_fs.delete_temp_calls == [("viking://temp/tmpdir", None)]
    assert summarize_calls[0]["temp_uris"] == ["viking://resources/root"]


@pytest.mark.asyncio
async def test_directory_semantic_ingest_commits_plan_before_summarizer(monkeypatch):
    from openviking.parse.output import ParseArtifactRef
    from openviking.storage.context_update_plan import (
        ContextUpdatePlan,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)
    ref = ParseArtifactRef(
        backend="agfs",
        root="viking://temp/tmpdir",
        resource_rel="root_tmp",
    )
    parse_result = SimpleNamespace(
        temp_dir_path=ref.root,
        source_path="x",
        source_format="repository",
        meta={},
        warnings=[],
        artifact_ref=ref,
        ensure_artifact_ref=lambda: ref,
    )
    plan = SemanticPlan(
        root_uri="viking://resources/root",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "added", "aggregate"),
                SemanticTreeEntry("a.py", "file", "added", "generate", md5="a"),
            )
        ),
    )
    context_plan = ContextUpdatePlan(plan.root_uri, "resource", semantic_plan=plan)
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(return_value=parse_result)
    rp.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(
                uri="viking://resources/root",
                temp_uri="viking://temp/tmpdir/root_tmp",
            ),
            _root_is_file=False,
        )
    )
    rp._commit_directory_artifact_with_plan = AsyncMock(return_value=context_plan)
    summarize = AsyncMock(return_value={"status": "success", "enqueued_count": 1})
    rp._summarizer = SimpleNamespace(summarize=summarize)

    result = await rp.process_resource(path="x", ctx=object(), build_index=True)

    assert result["status"] == "success"
    assert fake_fs.persist_calls == []
    kwargs = summarize.await_args.kwargs
    assert SemanticPlan.from_dict(kwargs["semantic_plan"]) == plan
    assert kwargs["temp_uris"] == ["viking://resources/root"]
    assert kwargs.get("artifact_ref") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_candidate", [False, True])
async def test_resource_processor_allows_flat_root_only_for_single_no_split_source(
    monkeypatch,
    auto_candidate,
):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    fake_fs.glob = AsyncMock(
        side_effect=NotADirectoryError("flat resource roots cannot be globbed")
    )
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/tmpdir",
            source_path="神雕_副本.md",
            source_format="markdown",
            meta={},
            warnings=[],
        )
    )
    root_uri = "viking://resources/神雕_副本.md"
    rp.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(
                uri=root_uri,
                temp_uri="viking://temp/tmpdir/神雕_副本/神雕_副本.md",
            ),
            _root_is_file=True,
            _candidate_uri=root_uri if auto_candidate else None,
        )
    )
    rp._commit_directory_artifact_with_plan = AsyncMock(
        return_value=ContextUpdatePlan(
            root_uri, "resource", file_refresh=FileRefreshIntent(root_uri, "new-md5")
        )
    )
    rp._summarizer = SimpleNamespace(
        summarize=AsyncMock(return_value={"status": "success"}),
        refresh_file_parent=AsyncMock(return_value={"status": "success"}),
    )

    result = await rp.process_resource(
        path="神雕_副本.md",
        ctx=object(),
        build_index=True,
        parse_mode="no_split",
    )

    assert result["status"] == "success"
    assert result["root_uri"] == root_uri
    assert fake_fs._async_agfs.exact_attempts == [("/mock/resources/神雕_副本.md", 0.0)]
    assert fake_fs._async_agfs.tree_attempts == []
    assert rp.tree_builder.finalize_from_temp.await_args.kwargs["flatten_single_file"] is True


@pytest.mark.asyncio
async def test_resource_processor_keeps_wrapper_for_directory_to_no_split(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/tmpdir",
            source_path="神雕.md",
            source_format="markdown",
            meta={},
            warnings=[],
        )
    )
    rp.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(
                uri="viking://resources/0803_shendiao_01",
                temp_uri="viking://temp/tmpdir/神雕",
            ),
            _root_is_file=False,
        )
    )
    rp._commit_directory_artifact_with_plan = AsyncMock(
        return_value=_directory_plan("viking://resources/0803_shendiao_01")
    )
    rp._summarizer = SimpleNamespace(summarize=AsyncMock(return_value={"status": "success"}))

    result = await rp.process_resource(
        path="神雕.md",
        ctx=object(),
        to="viking://resources/0803_shendiao_01",
        to_is_directory=True,
        build_index=True,
        parse_mode="no_split",
    )

    assert result["root_uri"] == "viking://resources/0803_shendiao_01"
    assert rp.tree_builder.finalize_from_temp.await_args.kwargs["flatten_single_file"] is False


@pytest.mark.asyncio
async def test_resource_processor_second_add_preserves_temp_uri_for_incremental(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS(exists_result=True)
    summarize_calls = []

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/tmpdir",
            source_path="x",
            source_format="text",
            meta={},
            warnings=[],
        )
    )

    context_tree = SimpleNamespace(
        root=SimpleNamespace(uri="viking://resources/root", temp_uri="viking://temp/root_tmp")
    )
    rp.tree_builder.finalize_from_temp = AsyncMock(return_value=context_tree)
    rp._commit_directory_artifact_with_plan = AsyncMock(
        return_value=_directory_plan("viking://resources/root")
    )
    rp._summarizer = SimpleNamespace(
        summarize=AsyncMock(
            side_effect=lambda *args, **kwargs: (
                summarize_calls.append(kwargs) or {"status": "success"}
            )
        )
    )

    result = await rp.process_resource(path="x", ctx=object(), build_index=True)

    assert result["status"] == "success"
    assert result["root_uri"] == "viking://resources/root"
    assert summarize_calls[0]["temp_uris"] == ["viking://resources/root"]
    assert fake_fs.persist_calls == []


@pytest.mark.asyncio
async def test_resource_processor_releases_resolved_candidate_when_acl_denied(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.exceptions import PermissionDeniedError

    fake_pathlock = _FakePathLock(busy_tree_paths={"/mock/resources/root_1"})
    fake_fs = _FakeVikingFS(existing_uris={"viking://resources/root"}, pathlock=fake_pathlock)
    fake_fs.prepare_acl_update = AsyncMock(side_effect=PermissionDeniedError("ACL denied"))
    fake_pathlock.pathlock_release = AsyncMock()

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    _patch_viking_fs(monkeypatch, fake_fs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/tmpdir",
            source_path="x",
            source_format="text",
            meta={},
            warnings=[],
        )
    )

    context_tree = SimpleNamespace(
        root=SimpleNamespace(uri="viking://resources/root", temp_uri="viking://temp/root_tmp"),
        _candidate_uri="viking://resources/root",
    )
    rp.tree_builder.finalize_from_temp = AsyncMock(return_value=context_tree)
    rp._commit_directory_artifact_with_plan = AsyncMock(
        side_effect=lambda **kwargs: _directory_plan(kwargs["root_uri"])
    )
    with pytest.raises(PermissionDeniedError):
        await rp.process_resource(
            path="x", ctx=object(), build_index=True, acl={"acl_mode": "inherit"}
        )

    assert fake_pathlock.acquired_tree_paths == ["/mock/resources/root_2"]
    fake_pathlock.pathlock_release.assert_awaited_once_with({"id": "lock-1"})
    rp._commit_directory_artifact_with_plan.assert_not_awaited()
