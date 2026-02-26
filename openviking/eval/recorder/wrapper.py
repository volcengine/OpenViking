# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Recorder wrapper for VikingFS and VikingDB.

Wraps existing storage backends to record IO operations.
"""

import time
from typing import Any, Dict, List, Optional

from openviking.eval.recorder import (
    AGFSCallRecord,
    IORecorder,
    get_recorder,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class _AGFSCallCollector:
    """
    Helper to collect AGFS calls from a wrapped AGFS client.

    This wraps an AGFS client and collects all calls made through it.
    """

    def __init__(self, agfs_client: Any):
        self._agfs = agfs_client
        self.calls: List[AGFSCallRecord] = []

    def __getattr__(self, name: str):
        original_attr = getattr(self._agfs, name)
        if not callable(original_attr):
            return original_attr

        def wrapped(*args, **kwargs):
            start_time = time.time()
            request = {"args": args, "kwargs": kwargs}
            success = True
            error = None
            response = None

            try:
                response = original_attr(*args, **kwargs)
                return response
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                latency_ms = (time.time() - start_time) * 1000
                call = AGFSCallRecord(
                    operation=name,
                    request=request,
                    response=response,
                    latency_ms=latency_ms,
                    success=success,
                    error=error,
                )
                self.calls.append(call)

        return wrapped


class RecordingVikingFS:
    """
    Wrapper for VikingFS that records all operations.

    This wrapper records VikingFS operations at two levels:
    1. VikingFS level: One record per VikingFS operation
    2. AGFS level: Collects all internal AGFS calls made during the operation

    Usage:
        from openviking.eval.recorder import init_recorder
        from openviking.eval.recorder.wrapper import RecordingVikingFS

        init_recorder(enabled=True)
        fs = RecordingVikingFS(viking_fs)
        await fs.read(uri)  # This will be recorded
    """

    def __init__(self, viking_fs: Any, recorder: Optional[IORecorder] = None):
        """
        Initialize wrapper.

        Args:
            viking_fs: VikingFS instance to wrap
            recorder: IORecorder instance (uses global if None)
        """
        self._fs = viking_fs
        self._recorder = recorder or get_recorder()
        self._original_agfs = getattr(viking_fs, "agfs", None)

    def __getattr__(self, name: str) -> Any:
        """
        Smart attribute getter that wraps async methods for recording.

        This will automatically wrap all async methods of VikingFS,
        ensuring every operation is recorded.
        """
        original_attr = getattr(self._fs, name)

        if not callable(original_attr) or name.startswith("_"):
            return original_attr
        # viking_fs文件操作
        if name not in (
            "ls",
            "mkdir",
            "stat",
            "rm",
            "mv",
            "read",
            "write",
            "grep",
            "glob",
            "tree",
            "abstract",
            "overview",
            "relations",
            "link",
            "unlink",
            "write_file",
            "read_file",
            "read_file_bytes",
            "write_file_bytes",
            "append_file",
            "move_file",
            "delete_temp",
            "write_context",
            "get_relations",
            "get_relations_with_content",
            "find",
            "search",
        ):
            return original_attr

        async def wrapped_async(*args, **kwargs):
            request = self._build_request(name, args, kwargs)
            start_time = time.time()

            collector = _AGFSCallCollector(self._fs.agfs)
            self._fs.agfs = collector

            try:
                result = await original_attr(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000
                self._recorder.record_fs(
                    operation=name,
                    request=request,
                    response=result,
                    latency_ms=latency_ms,
                    success=True,
                    error=None,
                    agfs_calls=collector.calls,
                )
                return result
            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                self._recorder.record_fs(
                    operation=name,
                    request=request,
                    response=None,
                    latency_ms=latency_ms,
                    success=False,
                    error=str(e),
                    agfs_calls=collector.calls,
                )
                raise
            finally:
                self._fs.agfs = self._original_agfs

        def wrapped_sync(*args, **kwargs):
            request = self._build_request(name, args, kwargs)
            start_time = time.time()

            try:
                result = original_attr(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000
                self._recorder.record_fs(
                    operation=name,
                    request=request,
                    response=result,
                    latency_ms=latency_ms,
                    success=True,
                    error=None,
                    agfs_calls=[],
                )
                return result
            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                self._recorder.record_fs(
                    operation=name,
                    request=request,
                    response=None,
                    latency_ms=latency_ms,
                    success=False,
                    error=str(e),
                    agfs_calls=[],
                )
                raise

        import inspect

        if inspect.iscoroutinefunction(original_attr) or name.startswith("_"):
            return wrapped_async

        return wrapped_async

    def _build_request(self, name: str, args: tuple, kwargs: dict) -> Dict[str, Any]:
        """
        Build request dict from method arguments.

        Args:
            name: Method name
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            Request dictionary
        """
        request = {}

        param_names = []
        try:
            import inspect

            original_attr = getattr(self._fs, name, None)
            if original_attr and callable(original_attr):
                sig = inspect.signature(original_attr)
                param_names = list(sig.parameters.keys())
        except Exception:
            pass

        if param_names:
            for i, arg in enumerate(args):
                if i < len(param_names):
                    param_name = param_names[i]
                    if param_name != "self":
                        request[param_name] = arg

        for key, value in kwargs.items():
            request[key] = value

        return request


class RecordingVikingDB:
    """
    Wrapper for vector store instances that records all operations.

    Usage:
        from openviking.eval.recorder import init_recorder
        from openviking.eval.recorder.wrapper import RecordingVikingDB

        init_recorder(enabled=True)
        db = RecordingVikingDB(vector_store)
        await db.search(...)  # This will be recorded
    """

    def __init__(self, viking_db: Any, recorder: Optional[IORecorder] = None):
        """
        Initialize wrapper.

        Args:
            viking_db: Vector store instance to wrap
            recorder: IORecorder instance (uses global if None)
        """
        self._db = viking_db
        self._recorder = recorder or get_recorder()

    def _record(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Record a VikingDB operation."""
        self._recorder.record_vikingdb(
            operation=operation,
            request=request,
            response=response,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    async def insert(self, collection: str, data: Dict[str, Any]) -> str:
        """Insert with recording."""
        request = {"collection": collection, "data": data}
        start_time = time.time()
        try:
            result = await self._db.upsert(data)
            latency_ms = (time.time() - start_time) * 1000
            self._record("insert", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("insert", request, None, latency_ms, False, str(e))
            raise

    async def update(self, collection: str, id: str, data: Dict[str, Any]) -> bool:
        """Update with recording."""
        request = {"collection": collection, "id": id, "data": data}
        start_time = time.time()
        try:
            existing = await self._db.get([id])
            if not existing:
                result = False
            else:
                payload = {**existing[0], **data, "id": id}
                result = bool(await self._db.upsert(payload))
            latency_ms = (time.time() - start_time) * 1000
            self._record("update", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("update", request, None, latency_ms, False, str(e))
            raise

    async def upsert(self, collection: str, data: Dict[str, Any]) -> str:
        """Upsert with recording."""
        request = {"collection": collection, "data": data}
        start_time = time.time()
        try:
            result = await self._db.upsert(data)
            latency_ms = (time.time() - start_time) * 1000
            self._record("upsert", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("upsert", request, None, latency_ms, False, str(e))
            raise

    async def delete(self, collection: str, ids: List[str]) -> int:
        """Delete with recording."""
        request = {"collection": collection, "ids": ids}
        start_time = time.time()
        try:
            result = await self._db.delete(ids)
            latency_ms = (time.time() - start_time) * 1000
            self._record("delete", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("delete", request, None, latency_ms, False, str(e))
            raise

    async def get(self, collection: str, ids: List[str]) -> List[Dict[str, Any]]:
        """Get with recording."""
        request = {"collection": collection, "ids": ids}
        start_time = time.time()
        try:
            result = await self._db.get(ids)
            latency_ms = (time.time() - start_time) * 1000
            self._record("get", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("get", request, None, latency_ms, False, str(e))
            raise

    async def exists(self, collection: str, id: str) -> bool:
        """Exists with recording."""
        request = {"collection": collection, "id": id}
        start_time = time.time()
        try:
            result = await self._db.exists(id)
            latency_ms = (time.time() - start_time) * 1000
            self._record("exists", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("exists", request, None, latency_ms, False, str(e))
            raise

    async def search(
        self,
        collection: str,
        vector: List[float],
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search with recording."""
        request = {"collection": collection, "vector": vector, "top_k": top_k, "filter": filter}
        start_time = time.time()
        try:
            result = await self._db.search(
                query_vector=vector,
                filter=filter,
                limit=top_k,
            )
            latency_ms = (time.time() - start_time) * 1000
            self._record("search", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("search", request, None, latency_ms, False, str(e))
            raise

    async def filter(
        self,
        collection: str,
        filter: Dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Filter with recording."""
        request = {"collection": collection, "filter": filter, "limit": limit, "offset": offset}
        start_time = time.time()
        try:
            result = await self._db.filter(
                filter=filter,
                limit=limit,
                offset=offset,
            )
            latency_ms = (time.time() - start_time) * 1000
            self._record("filter", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("filter", request, None, latency_ms, False, str(e))
            raise

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        """Create collection with recording."""
        request = {"name": name, "schema": schema}
        start_time = time.time()
        try:
            result = await self._db.create_collection(name, schema)
            latency_ms = (time.time() - start_time) * 1000
            self._record("create_collection", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("create_collection", request, None, latency_ms, False, str(e))
            raise

    async def drop_collection(self) -> bool:
        """Drop collection with recording."""
        request = {}
        start_time = time.time()
        try:
            result = await self._db.drop_collection()
            latency_ms = (time.time() - start_time) * 1000
            self._record("drop_collection", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("drop_collection", request, None, latency_ms, False, str(e))
            raise

    async def collection_exists(self) -> bool:
        """Check collection exists with recording."""
        request = {}
        start_time = time.time()
        try:
            result = await self._db.collection_exists()
            latency_ms = (time.time() - start_time) * 1000
            self._record("collection_exists", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("collection_exists", request, None, latency_ms, False, str(e))
            raise

    def __getattr__(self, name: str) -> Any:
        """Pass through any other attributes to the wrapped db."""
        return getattr(self._db, name)
