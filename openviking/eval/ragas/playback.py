# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Playback module for IORecorder.

Replays recorded IO operations and compares performance across different backends.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.eval.recorder import IORecord, IOType
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PlaybackResult:
    """
    Result of a single playback operation.

    Attributes:
        record: Original IO record
        playback_latency_ms: Latency during playback
        playback_success: Whether playback succeeded
        playback_error: Error message if failed
        response_match: Whether response matches original
    """

    record: IORecord
    playback_latency_ms: float = 0.0
    playback_success: bool = True
    playback_error: Optional[str] = None
    response_match: Optional[bool] = None


@dataclass
class PlaybackStats:
    """
    Statistics for playback session.

    Attributes:
        total_records: Total number of records played
        success_count: Number of successful operations
        error_count: Number of failed operations
        total_original_latency_ms: Total original latency
        total_playback_latency_ms: Total playback latency
        fs_stats: Statistics for FS operations
        vikingdb_stats: Statistics for VikingDB operations
        viking_fs_success_count: VikingFS operation success count
        viking_fs_error_count: VikingFS operation error count
        agfs_fs_success_count: AGFS FS operation success count
        agfs_fs_error_count: AGFS FS operation error count
        total_agfs_calls: Total number of AGFS calls across all VikingFS operations
        total_viking_fs_operations: Total number of VikingFS operations with AGFS calls
    """

    total_records: int = 0
    success_count: int = 0
    error_count: int = 0
    total_original_latency_ms: float = 0.0
    total_playback_latency_ms: float = 0.0
    fs_stats: Dict[str, Dict[str, Any]] = None
    vikingdb_stats: Dict[str, Dict[str, Any]] = None
    viking_fs_success_count: int = 0
    viking_fs_error_count: int = 0
    agfs_fs_success_count: int = 0
    agfs_fs_error_count: int = 0
    total_agfs_calls: int = 0
    total_viking_fs_operations: int = 0

    def __post_init__(self):
        if self.fs_stats is None:
            self.fs_stats = {}
        if self.vikingdb_stats is None:
            self.vikingdb_stats = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        viking_fs_success_rate = (
            self.viking_fs_success_count / self.total_viking_fs_operations * 100
            if self.total_viking_fs_operations > 0
            else 0
        )
        agfs_fs_total = self.agfs_fs_success_count + self.agfs_fs_error_count
        agfs_fs_success_rate = (
            self.agfs_fs_success_count / agfs_fs_total * 100
            if agfs_fs_total > 0
            else 0
        )
        avg_agfs_calls = (
            self.total_agfs_calls / self.total_viking_fs_operations
            if self.total_viking_fs_operations > 0
            else 0
        )

        return {
            "total_records": self.total_records,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "total_original_latency_ms": self.total_original_latency_ms,
            "total_playback_latency_ms": self.total_playback_latency_ms,
            "speedup_ratio": (
                self.total_original_latency_ms / self.total_playback_latency_ms
                if self.total_playback_latency_ms > 0
                else 0
            ),
            "viking_fs_stats": {
                "success_count": self.viking_fs_success_count,
                "error_count": self.viking_fs_error_count,
                "success_rate_percent": viking_fs_success_rate,
                "total_operations": self.total_viking_fs_operations,
                "avg_agfs_calls_per_operation": avg_agfs_calls,
            },
            "agfs_fs_stats": {
                "success_count": self.agfs_fs_success_count,
                "error_count": self.agfs_fs_error_count,
                "success_rate_percent": agfs_fs_success_rate,
                "total_calls": agfs_fs_total,
            },
            "fs_stats": self.fs_stats,
            "vikingdb_stats": self.vikingdb_stats,
        }


class _AGFSCallCollector:
    """
    Helper class to collect AGFS calls during playback for comparison.
    """

    def __init__(self, agfs_client: Any):
        self._agfs = agfs_client
        self.calls: List[Dict[str, Any]] = []

    def __getattr__(self, name: str):
        original_attr = getattr(self._agfs, name)
        if not callable(original_attr):
            return original_attr

        def wrapped(*args, **kwargs):
            call_record = {
                "operation": name,
                "request": {"args": args, "kwargs": kwargs},
                "success": True,
                "error": None,
            }
            try:
                response = original_attr(*args, **kwargs)
                call_record["response"] = response
                return response
            except Exception as e:
                call_record["success"] = False
                call_record["error"] = str(e)
                raise
            finally:
                self.calls.append(call_record)

        return wrapped


class IOPlayback:
    """
    Playback recorded IO operations.

    Replays recorded operations against a target backend and measures performance.

    Usage:
        playback = IOPlayback(config_file="./ov.conf")
        stats = await playback.play(record_file="./records/io_recorder_20260214.jsonl")
        print(stats.to_dict())
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        compare_response: bool = False,
        fail_fast: bool = False,
        enable_fs: bool = True,
        enable_vikingdb: bool = True,
        check_agfs_calls: bool = True,
    ):
        """
        Initialize IOPlayback.

        Args:
            config_file: Path to OpenViking config file (ov.conf)
            compare_response: Whether to compare playback response with original
            fail_fast: Stop on first error
            enable_fs: Whether to play FS operations
            enable_vikingdb: Whether to play VikingDB operations
            check_agfs_calls: Whether to check AGFS calls match recorded calls
        """
        self.config_file = config_file
        self.compare_response = compare_response
        self.fail_fast = fail_fast
        self.enable_fs = enable_fs
        self.enable_vikingdb = enable_vikingdb
        self.check_agfs_calls = check_agfs_calls
        self._viking_fs = None
        self._vector_store = None

    def _path_to_uri(self, path: str) -> str:
        """Convert AGFS path to VikingFS URI."""
        return self._viking_fs._path_to_uri(path)

    def _init_backends(self) -> None:
        """Initialize backend clients from config."""
        if self.config_file:
            import os

            os.environ["OPENVIKING_CONFIG_FILE"] = self.config_file

        from openviking.agfs_manager import AGFSManager
        from openviking.storage.viking_fs import init_viking_fs
        from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
        from openviking_cli.utils.config import get_openviking_config
        from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

        config = get_openviking_config()

        agfs_url = config.storage.agfs.url
        agfs_manager = None

        if config.storage.agfs.backend == "local":
            agfs_manager = AGFSManager(config=config.storage.agfs)
            agfs_manager.start()
            agfs_url = agfs_manager.url
            logger.info(f"[IOPlayback] Started AGFS at {agfs_url}")
        elif config.storage.agfs.backend in ["s3", "memory"]:
            agfs_manager = AGFSManager(config=config.storage.agfs)
            agfs_manager.start()
            agfs_url = agfs_manager.url
            logger.info(
                f"[IOPlayback] Started AGFS with {config.storage.agfs.backend} backend at {agfs_url}"
            )
        elif not agfs_url:
            agfs_url = "http://localhost:8080"
            logger.warning(f"[IOPlayback] No AGFS URL configured, using default: {agfs_url}")

        vector_store = None
        if self.enable_vikingdb:
            vectordb_config = config.storage.vectordb
            backend_config = VectorDBBackendConfig(
                backend=vectordb_config.backend or "local",
                path=vectordb_config.path or "./data/vectordb",
                url=vectordb_config.url,
                dimension=config.embedding.dimension,
            )
            if vectordb_config.volcengine:
                backend_config.volcengine = vectordb_config.volcengine
            vector_store = VikingVectorIndexBackend(config=backend_config)

        if self.enable_fs:
            self._viking_fs = init_viking_fs(
                agfs_url=agfs_url,
                vector_store=vector_store,
            )
        self._vector_store = vector_store

        logger.info(
            f"[IOPlayback] Initialized with config: {self.config_file}, "
            f"fs={self.enable_fs}, vikingdb={self.enable_vikingdb}"
        )

    async def _play_fs_operation(self, record: IORecord) -> PlaybackResult:
        """Play a single FS operation."""
        result = PlaybackResult(record=record)
        start_time = time.time()
        args0 = None

        try:
            operation = record.operation
            request = record.request

            if "args" in request or "kwargs" in request:
                args = request.get("args", [])
                kwargs = request.get("kwargs", {})
                args0 = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
            else:
                args = []
                kwargs = request
                args0 = kwargs.get("path", kwargs.get("uri", ""))

            collector = None
            original_agfs = None
            if self.check_agfs_calls and hasattr(record, "agfs_calls") and record.agfs_calls:
                collector = _AGFSCallCollector(self._viking_fs.agfs)
                original_agfs = self._viking_fs.agfs
                self._viking_fs.agfs = collector

            def process_arg(arg: Any) -> Any:
                if isinstance(arg, dict) and "__bytes__" in arg:
                    return arg["__bytes__"].encode("utf-8")
                if isinstance(arg, dict):
                    return {k: process_arg(v) for k, v in arg.items()}
                if isinstance(arg, list):
                    return [process_arg(item) for item in arg]
                return arg

            processed_args = [process_arg(arg) for arg in args]
            processed_kwargs = {k: process_arg(v) for k, v in kwargs.items()}

            method = getattr(self._viking_fs, operation)
            await method(*processed_args, **processed_kwargs)

            if collector and original_agfs:
                self._viking_fs.agfs = original_agfs
                result.response_match = self._compare_agfs_calls(record.agfs_calls, collector.calls)
                if not result.response_match:
                    result.playback_error = "AGFS calls mismatch"

            result.playback_latency_ms = (time.time() - start_time) * 1000
            result.playback_success = True

        except Exception as e:
            if original_agfs:
                self._viking_fs.agfs = original_agfs
            result.playback_latency_ms = (time.time() - start_time) * 1000
            playback_error = str(e)

            if record.error and self._errors_match(playback_error, record.error):
                result.playback_success = True
                result.playback_error = f"Matched original error: {playback_error}"
            else:
                result.playback_success = False
                result.playback_error = playback_error
                logger.error(f"[IOPlayback] FS {operation} on {args0} failed: {e}")

        return result

    def _compare_agfs_calls(self, recorded_calls: List[Any], actual_calls: List[Dict[str, Any]]) -> bool:
        """
        Compare recorded AGFS calls with actual AGFS calls.

        Args:
            recorded_calls: List of recorded AGFS calls (AGFSCallRecord or dict)
            actual_calls: List of actual AGFS calls (dicts)

        Returns:
            True if calls match, False otherwise
        """
        if len(recorded_calls) != len(actual_calls):
            logger.warning(
                f"AGFS call count mismatch: recorded {len(recorded_calls)}, actual {len(actual_calls)}"
            )
            return False

        for recorded_call, actual_call in zip(recorded_calls, actual_calls):
            if isinstance(recorded_call, dict):
                recorded_op = recorded_call.get("operation")
                recorded_req = recorded_call.get("request")
                recorded_success = recorded_call.get("success", True)
            else:
                recorded_op = recorded_call.operation
                recorded_req = recorded_call.request
                recorded_success = recorded_call.success

            if recorded_op != actual_call["operation"]:
                logger.warning(
                    f"AGFS operation mismatch: recorded {recorded_op}, actual {actual_call['operation']}"
                )
                return False
            if recorded_req != actual_call["request"]:
                logger.warning(
                    f"AGFS request mismatch for operation {recorded_op}"
                )
                return False
            if recorded_success != actual_call["success"]:
                logger.warning(
                    f"AGFS success status mismatch for operation {recorded_op}"
                )
                return False

        return True

    def _errors_match(self, playback_error: str, record_error: str) -> bool:
        """Check if playback error matches original record error."""
        playback_lower = playback_error.lower()
        record_lower = record_error.lower()

        if playback_lower == record_lower:
            return True

        error_type_patterns = [
            (
                "no such file",
                ["no such file", "not found", "does not exist", "no such file or directory"],
            ),
            ("not a directory", ["not a directory", "not directory"]),
            ("is a directory", ["is a directory", "is directory"]),
            ("permission denied", ["permission denied", "access denied"]),
            ("already exists", ["already exists", "file exists", "directory already exists"]),
            ("directory not empty", ["directory not empty", "not empty"]),
            ("connection refused", ["connection refused", "server not running"]),
            ("timeout", ["timeout", "timed out"]),
            ("failed to stat", ["failed to stat", "stat failed"]),
        ]

        for _error_type, patterns in error_type_patterns:
            playback_match = any(p in playback_lower for p in patterns)
            record_match = any(p in record_lower for p in patterns)
            if playback_match and record_match:
                return True

        return False

    async def _play_vikingdb_operation(self, record: IORecord) -> PlaybackResult:
        """Play a single VikingDB operation."""
        result = PlaybackResult(record=record)
        start_time = time.time()

        try:
            operation = record.operation
            request = record.request

            args = request.get("args", [])
            kwargs = request.get("kwargs", {})

            if operation == "insert":
                await self._vector_store.insert(*args, **kwargs)
            elif operation == "update":
                await self._vector_store.update(*args, **kwargs)
            elif operation == "upsert":
                await self._vector_store.upsert(*args, **kwargs)
            elif operation == "delete":
                await self._vector_store.delete(*args, **kwargs)
            elif operation == "get":
                await self._vector_store.get(*args, **kwargs)
            elif operation == "exists":
                await self._vector_store.exists(*args, **kwargs)
            elif operation == "search":
                await self._vector_store.search(*args, **kwargs)
            elif operation == "filter":
                await self._vector_store.filter(*args, **kwargs)
            elif operation == "create_collection":
                await self._vector_store.create_collection(*args, **kwargs)
            elif operation == "drop_collection":
                await self._vector_store.drop_collection(*args, **kwargs)
            elif operation == "collection_exists":
                await self._vector_store.collection_exists(*args, **kwargs)
            elif operation == "list_collections":
                await self._vector_store.list_collections(*args, **kwargs)
            else:
                raise ValueError(f"Unknown VikingDB operation: {operation}")

            result.playback_latency_ms = (time.time() - start_time) * 1000
            result.playback_success = True

        except Exception as e:
            result.playback_latency_ms = (time.time() - start_time) * 1000
            playback_error = str(e)

            if record.error and self._errors_match(playback_error, record.error):
                result.playback_success = True
                result.playback_error = f"Matched original error: {playback_error}"
            else:
                result.playback_success = False
                result.playback_error = playback_error
                logger.error(f"[IOPlayback] VikingDB {operation} failed: {e}")

        return result

    async def play_record(self, record: IORecord) -> PlaybackResult:
        """Play a single record."""
        if record.io_type == IOType.FS.value:
            if not self.enable_fs:
                return PlaybackResult(record=record, playback_success=True)
            return await self._play_fs_operation(record)
        else:
            if not self.enable_vikingdb:
                return PlaybackResult(record=record, playback_success=True)
            return await self._play_vikingdb_operation(record)

    async def play(
        self,
        record_file: str,
        limit: Optional[int] = None,
        offset: int = 0,
        io_type: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> PlaybackStats:
        """
        Play all records from a record file.

        Args:
            record_file: Path to the record JSONL file
            limit: Maximum number of records to play
            offset: Number of records to skip
            io_type: Filter by IO type (fs or vikingdb)
            operation: Filter by operation name

        Returns:
            PlaybackStats with playback results
        """

        need_fs = self.enable_fs and (io_type is None or io_type == "fs")
        need_vikingdb = self.enable_vikingdb and (io_type is None or io_type == "vikingdb")

        if need_fs or need_vikingdb:
            self._init_backends()

        records = []
        with open(record_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(IORecord.from_dict(json.loads(line)))

        filtered_records = []
        for r in records:
            if io_type and r.io_type != io_type:
                continue
            if operation and r.operation != operation:
                continue
            if r.io_type == IOType.FS.value and not self.enable_fs:
                continue
            if r.io_type == IOType.VIKINGDB.value and not self.enable_vikingdb:
                continue
            filtered_records.append(r)

        records = filtered_records[offset:]
        if limit:
            records = records[:limit]

        stats = PlaybackStats(total_records=len(records))
        logger.info(f"[IOPlayback] Playing {len(records)} records from {record_file}")

        for i, record in enumerate(records):
            result = await self.play_record(record)

            stats.total_original_latency_ms += record.latency_ms
            stats.total_playback_latency_ms += result.playback_latency_ms

            if result.playback_success:
                stats.success_count += 1
            else:
                stats.error_count += 1

            op_key = f"{record.io_type}.{record.operation}"
            if record.io_type == IOType.FS.value:
                if op_key not in stats.fs_stats:
                    stats.fs_stats[op_key] = {
                        "count": 0,
                        "total_original_latency_ms": 0.0,
                        "total_playback_latency_ms": 0.0,
                    }
                stats.fs_stats[op_key]["count"] += 1
                stats.fs_stats[op_key]["total_original_latency_ms"] += record.latency_ms
                stats.fs_stats[op_key]["total_playback_latency_ms"] += result.playback_latency_ms

                if hasattr(record, "agfs_calls") and record.agfs_calls:
                    stats.total_viking_fs_operations += 1
                    if result.playback_success:
                        stats.viking_fs_success_count += 1
                    else:
                        stats.viking_fs_error_count += 1

                    stats.total_agfs_calls += len(record.agfs_calls)
                    for call in record.agfs_calls:
                        if call.success:
                            stats.agfs_fs_success_count += 1
                        else:
                            stats.agfs_fs_error_count += 1
            else:
                if op_key not in stats.vikingdb_stats:
                    stats.vikingdb_stats[op_key] = {
                        "count": 0,
                        "total_original_latency_ms": 0.0,
                        "total_playback_latency_ms": 0.0,
                    }
                stats.vikingdb_stats[op_key]["count"] += 1
                stats.vikingdb_stats[op_key]["total_original_latency_ms"] += record.latency_ms
                stats.vikingdb_stats[op_key]["total_playback_latency_ms"] += (
                    result.playback_latency_ms
                )

            if (i + 1) % 100 == 0:
                logger.info(f"[IOPlayback] Progress: {i + 1}/{len(records)}")

            if self.fail_fast and not result.playback_success:
                logger.error(f"[IOPlayback] Stopping due to error at record {i + 1}")
                break

        logger.info(
            f"[IOPlayback] Completed: {stats.success_count}/{stats.total_records} successful"
        )
        return stats

    def play_sync(self, **kwargs) -> PlaybackStats:
        """Synchronous wrapper for play method."""
        return asyncio.run(self.play(**kwargs))
