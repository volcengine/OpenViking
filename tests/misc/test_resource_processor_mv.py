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

    def measure(self, *args, **kwargs):
        return _CtxMgr()


class _CtxMgr:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyLockHandle:
    def __init__(self, handle_id: str = "lock-1"):
        self.id = handle_id


class _DummyLockManager:
    def __init__(self):
        self._handle = _DummyLockHandle()

    def create_handle(self):
        return self._handle

    async def acquire_subtree(self, handle, path):
        return True

    async def release(self, handle):
        return None

    def get_handle(self, handle_id):
        if handle_id == self._handle.id:
            return self._handle
        return None


class _DummyLockContext:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeVikingFS:
    def __init__(self, existing_meta: str | None = None):
        self.agfs = SimpleNamespace(mv=MagicMock(return_value={"status": "ok"}))
        self.existing_meta = existing_meta
        self.writes = []

    def bind_request_context(self, ctx):
        return _CtxMgr()

    async def exists(self, uri, ctx=None):
        return False

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        return None

    async def delete_temp(self, temp_dir_path, ctx=None):
        return None

    async def read(self, uri, ctx=None):
        if self.existing_meta is None:
            raise FileNotFoundError(uri)
        return self.existing_meta

    async def write(self, uri, content, ctx=None):
        self.writes.append((uri, content))
        return None

    def _uri_to_path(self, uri, ctx=None):
        return f"/mock/{uri.replace('viking://', '')}"


@pytest.mark.asyncio
async def test_resource_processor_first_add_persist_does_not_await_agfs_mv(monkeypatch):
    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS()
    fake_lock_manager = _DummyLockManager()

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager", lambda: fake_lock_manager
    )
    monkeypatch.setattr("openviking.storage.transaction.LockContext", _DummyLockContext)

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


@pytest.mark.asyncio
async def test_resource_processor_tags_merge_meta_json(monkeypatch):
    import json

    from openviking.utils.resource_processor import ResourceProcessor

    fake_fs = _FakeVikingFS(existing_meta='{"name":"demo","owner":"alice"}')
    fake_lock_manager = _DummyLockManager()

    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager", lambda: fake_lock_manager
    )
    monkeypatch.setattr("openviking.storage.transaction.LockContext", _DummyLockContext)

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

    result = await rp.process_resource(
        path="x", ctx=object(), build_index=False, summarize=False, tags="tag-a,tag-b"
    )

    assert result["status"] == "success"
    assert len(fake_fs.writes) == 1
    write_uri, write_content = fake_fs.writes[0]
    assert write_uri == "viking://resources/root/.meta.json"
    merged_meta = json.loads(write_content)
    assert merged_meta["name"] == "demo"
    assert merged_meta["owner"] == "alice"
    assert merged_meta["tags"] == "tag-a,tag-b"
