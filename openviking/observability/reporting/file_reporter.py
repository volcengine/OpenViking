"""Write complete business events to rotating JSON lines for a local log collector."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from openviking.usage_reporter.file_log_sink import ProcessSafeTimedRotatingFileHandler

from .reporter import _warn_count


class _MetricFileHandler(ProcessSafeTimedRotatingFileHandler):
    def handleError(self, record):
        raise


class FileEventReporter:
    def __init__(self, config: dict):
        settings = config["file_log"]
        path = str(settings.get("path") or "").strip()
        capacity = int(config.get("telemetry", {}).get("queue_capacity", 1024))
        interval = int(settings.get("rotation_interval_hours", 1))
        backups = int(settings.get("backup_count", 168))
        if not path or capacity <= 0 or interval <= 0 or backups < 0:
            raise ValueError("invalid file reporting path, queue capacity or rotation settings")
        self.path = Path(os.path.expandvars(os.path.expanduser(path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = _MetricFileHandler(
            filename=self.path,
            when="H",
            interval=interval,
            backupCount=backups,
            encoding="utf-8",
            utc=True,
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self.queue: queue.Queue[str] = queue.Queue(maxsize=capacity)
        self.sent = self.failed = self.dropped = 0
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="ov-event-file-writer", daemon=True)
        self._thread.start()

    def report(self, event: dict) -> bool:
        try:
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            with self._lock:
                if self._closed:
                    raise ValueError("file reporter is closed")
                self.queue.put_nowait(payload)
            return True
        except (ValueError, TypeError, queue.Full):
            with self._lock:
                self.dropped += 1
                dropped = self.dropped
            _warn_count("File event exporter dropped events", dropped)
            return False

    def _run(self):
        try:
            while True:
                try:
                    payload = self.queue.get(timeout=0.1)
                except queue.Empty:
                    with self._lock:
                        if self._closed:
                            return
                    continue
                try:
                    record = logging.LogRecord(__name__, logging.INFO, "", 0, payload, (), None)
                    self._handler.handle(record)
                    with self._lock:
                        self.sent += 1
                except Exception:
                    with self._lock:
                        self.failed += 1
                        failed = self.failed
                    _warn_count("File event exporter write failed", failed)
                finally:
                    self.queue.task_done()
        finally:
            self._handler.close()

    def report_committed(self, event: dict) -> None:
        """Write low-volume registry changes before returning to the mutation caller."""
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._lock:
            if self._closed:
                raise ValueError("file reporter is closed")
            record = logging.LogRecord(__name__, logging.INFO, "", 0, payload, (), None)
            self._handler.handle(record)
            self.sent += 1

    def flush(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self.queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                raise TimeoutError("file event exporter flush timed out")
            time.sleep(0.01)
        if self.failed:
            raise RuntimeError("file event exporter encountered write failures")

    def close(self, timeout=5.0):
        with self._lock:
            self._closed = True
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("file event exporter shutdown still draining")
        if self.failed:
            raise RuntimeError("file event exporter closed with write failures")
