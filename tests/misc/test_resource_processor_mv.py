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


class _FakeLockManager:
    def __init__(self, *, busy_tree_paths=None):
        from openviking.storage.transaction.lock_handle import LockHandle

        self._lock_handle_cls = LockHandle
        self._handles = {}
        self.acquired_exact_paths = []
        self.acquired_tree_paths = []
        self.tree_attempts = []
        self.busy_tree_paths = set(busy_tree_paths or [])

    def create_handle(self):
        handle = self._lock_handle_cls()
        self._handles[handle.id] = handle
        return handle

    async def acquire_exact_path_batch(self, handle, paths, timeout=None):
        for path in paths:
            lock_path = f"exact:{path}"
            handle.add_lock(lock_path)
            self.acquired_exact_paths.append(path)
        return True

    async def acquire_tree(self, handle, path, timeout=None):
        self.tree_attempts.append((path, timeout))
        if path in self.busy_tree_paths:
            return False
        lock_path = f"tree:{path}"
        handle.add_lock(lock_path)
        self.acquired_tree_paths.append(path)
        return True

    async def release_selected(self, handle, lock_paths):
        for path in lock_paths:
            handle.remove_lock(path)

    async def release(self, handle):
        for path in list(handle.locks):
            handle.remove_lock(path)
        self._handles.pop(handle.id, None)

    def get_handle(self, handle_id):
        handle = self._handles.get(handle_id)
        if handle and handle.locks:
            return handle
        return None


class _FakeVikingFS:
    def __init__(self, *, exists_result=False, existing_uris=None):
        self.agfs = SimpleNamespace(
            write=MagicMock(return_value={"status": "ok"}),
        )
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

    async def delete_temp(self, temp_dir_path, ctx=None):
        self.delete_temp_calls.append(temp_dir_path)
        return None

    async def persist_temp_tree(self, temp_uri, target_uri, ctx=None):
        self.persist_calls.append((temp_uri, target_uri))
        self.agfs.write(self._uri_to_path(target_uri, ctx=ctx), b"content")

    def _uri_to_path(self, uri, ctx=None):
        return f"/mock/{uri.replace('viking://', '')}"


@pytest.mark.asyncio
async def test_resource_processor_first_add_summarizes_from_committed_uri(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    fake_lock_manager = _FakeLockManager()
    summarize_calls = []

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: fake_lock_manager,
    )

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
    assert fake_fs.persist_calls == [("viking://temp/root_tmp", "viking://resources/root")]
    assert fake_fs.delete_temp_calls == ["viking://temp/tmpdir"]
    assert summarize_calls[0]["temp_uris"] == ["viking://resources/root"]
    assert summarize_calls[0]["target_preexisting"] is False


@pytest.mark.asyncio
async def test_resource_processor_second_add_preserves_temp_uri_for_incremental(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS(exists_result=True)
    fake_lock_manager = _FakeLockManager()
    summarize_calls = []

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: fake_lock_manager,
    )

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

    fake_fs = _FakeVikingFS(existing_uris={"viking://resources/root"})
    fake_lock_manager = _FakeLockManager(busy_tree_paths={"/mock/resources/root_1"})
    summarize_calls = []

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: fake_lock_manager,
    )

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
    assert fake_lock_manager.tree_attempts == [
        ("/mock/resources/root_1", 0.0),
        ("/mock/resources/root_2", 0.0),
    ]
    assert fake_lock_manager.acquired_exact_paths == []
    assert fake_lock_manager.acquired_tree_paths == ["/mock/resources/root_2"]
    assert summarize_calls[0]["temp_uris"] == ["viking://resources/root_2"]
    assert summarize_calls[0]["target_preexisting"] is False
    assert fake_fs.persist_calls == [("viking://temp/root_tmp", "viking://resources/root_2")]
