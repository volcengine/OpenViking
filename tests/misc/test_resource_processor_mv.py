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
    def __init__(self, *, existing_dirs=None):
        from openviking.storage.transaction.lock_handle import LockHandle

        self._lock_handle_cls = LockHandle
        self._handles = {}
        self.acquired_exact_paths = []
        self.acquired_tree_paths = []
        self.existing_dirs = set(existing_dirs or [])
        self.released_lock_paths = []

    def _exact_lock_path(self, path):
        if path in self.existing_dirs:
            return f"{path}/.path.ovlock"
        return f"{path}/.exact.ovlock"

    def _tree_lock_path(self, path):
        return f"{path}/.path.ovlock"

    def create_handle(self):
        handle = self._lock_handle_cls()
        self._handles[handle.id] = handle
        return handle

    async def acquire_exact_path(self, handle, path, timeout=None):
        handle.add_lock(self._exact_lock_path(path))
        self.acquired_exact_paths.append(path)
        return True

    async def acquire_exact_path_batch(self, handle, paths, timeout=None):
        for path in paths:
            await self.acquire_exact_path(handle, path, timeout=timeout)
        return True

    async def acquire_tree(self, handle, path, timeout=None):
        handle.add_lock(self._tree_lock_path(path))
        self.acquired_tree_paths.append(path)
        return True

    async def release_selected(self, handle, lock_paths):
        for path in lock_paths:
            self.released_lock_paths.append(path)
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
    def __init__(self, *, exists_result=False):
        self.agfs = SimpleNamespace(mv=MagicMock(return_value={"status": "ok"}))
        self._exists_result = exists_result

    def bind_request_context(self, ctx):
        return _CtxMgr()

    async def exists(self, uri, ctx=None):
        return self._exists_result

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        return None

    async def delete_temp(self, temp_dir_path, ctx=None):
        return None

    def _uri_to_path(self, uri, ctx=None):
        return f"/mock/{uri.replace('viking://', '')}"


@pytest.mark.asyncio
async def test_resource_processor_first_add_persist_does_not_await_agfs_mv(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    fake_lock_manager = _FakeLockManager()

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

    result = await rp.process_resource(path="x", ctx=object(), build_index=False, summarize=False)

    assert result["status"] == "success"
    assert result["root_uri"] == "viking://resources/root"
    fake_fs.agfs.mv.assert_called_once()
    assert fake_lock_manager.acquired_exact_paths == ["/mock/resources/root"]
    assert fake_lock_manager.acquired_tree_paths == ["/mock/resources/root"]


@pytest.mark.asyncio
async def test_resource_processor_second_add_preserves_temp_uri_for_incremental(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS(exists_result=True)
    root_path = "/mock/resources/root"
    fake_lock_manager = _FakeLockManager(existing_dirs={root_path})
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
    fake_fs.agfs.mv.assert_not_called()
    assert fake_lock_manager.acquired_exact_paths == [root_path]
    assert fake_lock_manager.acquired_tree_paths == [root_path]
    assert fake_lock_manager.released_lock_paths == []
    assert fake_lock_manager.get_handle(summarize_calls[0]["lifecycle_lock_handle_id"])
