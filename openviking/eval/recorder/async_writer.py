# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async writer for IORecorder.

Uses a background thread to write records asynchronously, avoiding blocking the main thread.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _serialize_for_json(obj: Any) -> Any:
    """Serialize object for JSON compatibility."""
    if obj is None:
        return None
    if isinstance(obj, bytes):
        try:
            decoded = obj.decode("utf-8", errors="replace")
            return {"__bytes__": decoded, "__len__": len(obj)}
        except Exception:
            return {"__bytes__": f"<binary data: {len(obj)} bytes>", "__len__": len(obj)}
    if isinstance(obj, dict):
        return {_serialize_for_json(k): _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    if isinstance(obj, tuple):
        return [_serialize_for_json(item) for item in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "__dict__"):
        return {"__class__": type(obj).__name__, "data": str(obj)[:1000]}
    return str(obj)[:1000]


class AsyncRecordWriter:
    """
    Asynchronous record writer using a background thread.

    Writes IO records to a JSONL file without blocking the main thread.

    Usage:
        writer = AsyncRecordWriter("./records/io_recorder_20260214.jsonl")
        writer.write_record(record_dict)

        # On shutdown
        writer.stop()
    """

    def __init__(self, file_path: str, batch_size: int = 100, flush_interval: float = 1.0):
        """
        Initialize async writer.

        Args:
            file_path: Path to the output JSONL file
            batch_size: Number of records to batch before writing
            flush_interval: Maximum time (seconds) before flushing batch
        """
        self.file_path = Path(file_path)
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._queue: queue.Queue[Optional[Dict[str, Any]]] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._ensure_dir()
        self._start_writer()

    def _ensure_dir(self) -> None:
        """Ensure the output directory exists."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def _start_writer(self) -> None:
        """Start the background writer thread."""
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()

    def _writer_loop(self) -> None:
        """Background thread loop for writing records."""
        batch: list[Dict[str, Any]] = []
        last_flush = time.time()

        while not self._stop_event.is_set():
            try:
                record = self._queue.get(timeout=0.1)

                if record is None:
                    break

                batch.append(record)

                if (
                    len(batch) >= self.batch_size
                    or (time.time() - last_flush) >= self.flush_interval
                ):
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.time()

            except queue.Empty:
                if batch and (time.time() - last_flush) >= self.flush_interval:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.time()
                continue

        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[Dict[str, Any]]) -> None:
        """Write a batch of records to file."""
        if not batch:
            return

        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                for record in batch:
                    serialized_record = _serialize_for_json(record)
                    record_str = json.dumps(serialized_record, ensure_ascii=False)
                    f.write(record_str + "\n")
        except Exception as e:
            logger.critical(f"Failed to write records to {self.file_path}: {e}")
            logger.critical(
                "IO recording failed, exiting immediately to ensure playback correctness"
            )
            os._exit(1)

    def write_record(self, record: Dict[str, Any]) -> None:
        """
        Queue a record for writing.

        Args:
            record: Record dictionary to write
        """
        if self._stop_event.is_set():
            logger.critical("Writer is stopped, cannot write record - exiting immediately")
            os._exit(1)

        self._queue.put(record)

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stop the writer and flush remaining records.

        Args:
            timeout: Maximum time to wait for flush
        """
        if self._stop_event.is_set():
            return

        self._stop_event.set()
        self._queue.put(None)

        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Writer thread did not stop gracefully")

    def is_running(self) -> bool:
        """Check if the writer is running."""
        return not self._stop_event.is_set()
