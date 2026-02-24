# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Recording AGFS Client wrapper.

Wraps AGFSClient to record all IO operations for later playback.
"""

import time
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Union

from openviking.eval.recorder import IOType
from openviking.eval.recorder.async_writer import AsyncRecordWriter
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RecordingAGFSClient:
    """
    Wrapper for AGFSClient that records all operations.

    This wrapper intercepts all AGFS operations and records them
    to a file for later playback and performance analysis.

    Usage:
        from pyagfs import AGFSClient
        from openviking.eval.recorder.recording_client import RecordingAGFSClient

        base_client = AGFSClient(api_base_url="http://localhost:1833")
        recording_client = RecordingAGFSClient(base_client, "./records/io_recorder.jsonl")

        # Use recording_client as you would use AGFSClient
        result = recording_client.ls("/")

        # Stop recording when done
        recording_client.stop_recording()
    """

    def __init__(
        self,
        agfs_client: Any,
        record_file: str,
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ):
        """
        Initialize recording client.

        Args:
            agfs_client: The underlying AGFSClient instance
            record_file: Path to the record file
            batch_size: Number of records to batch before writing
            flush_interval: Maximum time (seconds) before flushing batch
        """
        self._client = agfs_client
        self._writer = AsyncRecordWriter(
            record_file,
            batch_size=batch_size,
            flush_interval=flush_interval,
        )
        logger.info(f"[RecordingAGFSClient] Recording to: {record_file}")

    def _record(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Record an operation asynchronously."""
        record = {
            "timestamp": datetime.now().isoformat(),
            "io_type": IOType.FS.value,
            "operation": operation,
            "request": self._serialize_response(request),
            "response": self._serialize_response(response),
            "latency_ms": latency_ms,
            "success": success,
            "error": str(error) if error else None,
        }
        self._writer.write_record(record)

    def _serialize_response(self, response: Any) -> Any:
        """Serialize response for JSON compatibility."""
        if response is None:
            return None
        if isinstance(response, bytes):
            try:
                decoded = response.decode("utf-8", errors="replace")
                return {"__bytes__": decoded, "__len__": len(response)}
            except Exception:
                return {
                    "__bytes__": f"<binary data: {len(response)} bytes>",
                    "__len__": len(response),
                }
        if isinstance(response, dict):
            return {k: self._serialize_response(v) for k, v in response.items()}
        if isinstance(response, list):
            return [self._serialize_response(v) for v in response]
        if isinstance(response, (str, int, float, bool)):
            return response
        if hasattr(response, "__dict__"):
            return {"__class__": type(response).__name__, "data": str(response)}
        return str(response)

    def _wrap_operation(self, operation: str, *args, **kwargs) -> Any:
        """Wrap an operation with recording."""
        request = {"args": list(args), "kwargs": dict(kwargs)}
        start_time = time.time()

        try:
            method = getattr(self._client, operation)
            result = method(*args, **kwargs)
            latency_ms = (time.time() - start_time) * 1000
            self._record(operation, request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record(operation, request, None, latency_ms, False, str(e))
            raise

    def stop_recording(self, timeout: float = 5.0) -> None:
        """Stop recording and flush remaining records."""
        self._writer.stop(timeout=timeout)

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False) -> Any:
        """Read file with recording."""
        return self._wrap_operation("read", path, offset, size, stream)

    def write(self, path: str, data: Union[bytes, Iterator[bytes]], max_retries: int = 3) -> str:
        """Write file with recording."""
        return self._wrap_operation("write", path, data, max_retries)

    def ls(self, path: str = "/") -> List[Dict[str, Any]]:
        """List directory with recording."""
        return self._wrap_operation("ls", path)

    def stat(self, path: str) -> Dict[str, Any]:
        """Get file info with recording."""
        return self._wrap_operation("stat", path)

    def mkdir(self, path: str, mode: str = "755") -> Dict[str, Any]:
        """Create directory with recording."""
        return self._wrap_operation("mkdir", path, mode)

    def rm(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        """Delete with recording."""
        return self._wrap_operation("rm", path, recursive)

    def mv(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Move with recording."""
        return self._wrap_operation("mv", old_path, new_path)

    def grep(
        self,
        path: str,
        pattern: str,
        recursive: bool = False,
        case_insensitive: bool = False,
        stream: bool = False,
    ) -> Any:
        """Grep with recording."""
        return self._wrap_operation("grep", path, pattern, recursive, case_insensitive, stream)

    def cat(self, path: str, offset: int = 0, size: int = -1, stream: bool = False) -> Any:
        """Cat file with recording."""
        return self._wrap_operation("cat", path, offset, size, stream)

    def chmod(self, path: str, mode: int) -> Dict[str, Any]:
        """Chmod with recording."""
        return self._wrap_operation("chmod", path, mode)

    def touch(self, path: str) -> Dict[str, Any]:
        """Touch with recording."""
        return self._wrap_operation("touch", path)

    def digest(self, path: str, algorithm: str = "xxh3") -> Dict[str, Any]:
        """Digest with recording."""
        return self._wrap_operation("digest", path, algorithm)

    def create(self, path: str) -> Dict[str, Any]:
        """Create file with recording."""
        return self._wrap_operation("create", path)

    def health(self) -> Dict[str, Any]:
        """Health check with recording."""
        return self._wrap_operation("health")

    def mounts(self) -> List[Dict[str, Any]]:
        """List mounts with recording."""
        return self._wrap_operation("mounts")

    def mount(self, fstype: str, path: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Mount with recording."""
        return self._wrap_operation("mount", fstype, path, config)

    def unmount(self, path: str) -> Dict[str, Any]:
        """Unmount with recording."""
        return self._wrap_operation("unmount", path)

    def open_handle(self, path: str, flags: int = 0, mode: int = 420, lease: int = 60) -> Any:
        """Open handle with recording."""
        return self._wrap_operation("open_handle", path, flags, mode, lease)

    def close_handle(self, handle_id: int) -> Dict[str, Any]:
        """Close handle with recording."""
        return self._wrap_operation("close_handle", handle_id)

    def handle_read(self, handle_id: int, size: int = -1, offset: Optional[int] = None) -> bytes:
        """Handle read with recording."""
        return self._wrap_operation("handle_read", handle_id, size, offset)

    def handle_write(self, handle_id: int, data: bytes, offset: Optional[int] = None) -> int:
        """Handle write with recording."""
        return self._wrap_operation("handle_write", handle_id, data, offset)

    def handle_seek(self, handle_id: int, offset: int, whence: int = 0) -> int:
        """Handle seek with recording."""
        return self._wrap_operation("handle_seek", handle_id, offset, whence)

    def handle_stat(self, handle_id: int) -> Dict[str, Any]:
        """Handle stat with recording."""
        return self._wrap_operation("handle_stat", handle_id)

    def handle_sync(self, handle_id: int) -> Dict[str, Any]:
        """Handle sync with recording."""
        return self._wrap_operation("handle_sync", handle_id)

    def renew_handle(self, handle_id: int, lease: int = 60) -> Dict[str, Any]:
        """Renew handle with recording."""
        return self._wrap_operation("renew_handle", handle_id, lease)

    def get_handle_info(self, handle_id: int) -> Dict[str, Any]:
        """Get handle info with recording."""
        return self._wrap_operation("get_handle_info", handle_id)

    def list_handles(self) -> List[Dict[str, Any]]:
        """List handles with recording."""
        return self._wrap_operation("list_handles")

    def list_plugins(self) -> List[str]:
        """List plugins with recording."""
        return self._wrap_operation("list_plugins")

    def get_plugins_info(self) -> List[dict]:
        """Get plugins info with recording."""
        return self._wrap_operation("get_plugins_info")

    def load_plugin(self, library_path: str) -> Dict[str, Any]:
        """Load plugin with recording."""
        return self._wrap_operation("load_plugin", library_path)

    def unload_plugin(self, library_path: str) -> Dict[str, Any]:
        """Unload plugin with recording."""
        return self._wrap_operation("unload_plugin", library_path)

    def __getattr__(self, name: str) -> Any:
        """Pass through any other attributes to the wrapped client."""
        return getattr(self._client, name)

    def __enter__(self) -> "RecordingAGFSClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop_recording()
