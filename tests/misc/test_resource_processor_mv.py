import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class _DummyVikingDB:
    def get_embedder(self):
        return None


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
    assert fake_fs.persist_calls == [
        ("viking://temp/root_tmp", "viking://resources/root", {"id": "lock-1"})
    ]
    assert fake_fs.delete_temp_calls == [("viking://temp/tmpdir", None)]
    assert summarize_calls[0]["temp_uris"] == ["viking://resources/root"]
    assert summarize_calls[0]["target_preexisting"] is False


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
    assert fake_fs._async_agfs.exact_attempts == [
        ("/mock/resources/神雕_副本.md", 0.0)
    ]
    assert fake_fs._async_agfs.tree_attempts == []
    assert rp.tree_builder.finalize_from_temp.await_args.kwargs[
        "flatten_single_file"
    ] is True


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
    rp._summarizer = SimpleNamespace(
        summarize=AsyncMock(return_value={"status": "success"})
    )

    result = await rp.process_resource(
        path="神雕.md",
        ctx=object(),
        to="viking://resources/0803_shendiao_01",
        to_is_directory=True,
        build_index=True,
        parse_mode="no_split",
    )

    assert result["root_uri"] == "viking://resources/0803_shendiao_01"
    assert rp.tree_builder.finalize_from_temp.await_args.kwargs[
        "flatten_single_file"
    ] is False


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
    assert summarize_calls[0]["temp_uris"] == ["viking://temp/root_tmp"]
    assert summarize_calls[0]["target_preexisting"] is True
    assert fake_fs.persist_calls == []


@pytest.mark.asyncio
async def test_resource_processor_auto_candidate_skips_existing_and_busy(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_pathlock = _FakePathLock(busy_tree_paths={"/mock/resources/root_1"})
    fake_fs = _FakeVikingFS(existing_uris={"viking://resources/root"}, pathlock=fake_pathlock)
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
        root=SimpleNamespace(uri="viking://resources/root", temp_uri="viking://temp/root_tmp"),
        _candidate_uri="viking://resources/root",
    )
    rp.tree_builder.finalize_from_temp = AsyncMock(return_value=context_tree)
    rp._summarizer = SimpleNamespace(
        summarize=AsyncMock(
            side_effect=lambda *args, **kwargs: (
                summarize_calls.append(kwargs) or {"status": "success"}
            )
        )
    )

    result = await rp.process_resource(path="x", ctx=object(), build_index=True)

    assert result["status"] == "success"
    assert result["root_uri"] == "viking://resources/root_2"
    assert fake_fs.exists_calls == [
        "viking://resources/root",
        "viking://resources/root_1",
        "viking://resources/root_2",
    ]
    assert fake_pathlock.tree_attempts == [
        ("/mock/resources/root_1", 0.0),
        ("/mock/resources/root_2", 0.0),
    ]
    assert fake_pathlock.acquired_tree_paths == ["/mock/resources/root_2"]
    assert summarize_calls[0]["temp_uris"] == ["viking://resources/root_2"]
    assert summarize_calls[0]["target_preexisting"] is False
    assert fake_fs.persist_calls == [
        ("viking://temp/root_tmp", "viking://resources/root_2", {"id": "lock-1"})
    ]
