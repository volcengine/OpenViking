# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Log files written off the event loop, and the status line a process logs on a timer."""

import asyncio
import atexit
import logging
import queue
import sys
import time
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

import orjson

from openviking_cli.utils.logger import CommonLogFormatter, TraceContextFilter

from .client import CALL

logger = logging.getLogger(__name__)
snapshot_logger = logging.getLogger("openviking_gateway.snapshot")
QUEUE_SIZE = 10000
BACKUPS = 4
LOG_MAX_BYTES = 256 * 1024 * 1024


class _Listener(QueueListener):
    def stop(self):
        # Safe to call twice: at close and again at exit.
        if self._thread:
            super().stop()

    def enqueue_sentinel(self):
        # Wait for room: the listener must see the sentinel even behind a full queue.
        self.queue.put(self._sentinel)


def rotating_file(path, max_bytes, formatter):
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=BACKUPS, encoding="utf-8")
    handler.setFormatter(formatter)
    return handler


class DroppingQueueHandler(QueueHandler):
    """Hand records to a listener thread; a full queue drops the record and counts it."""

    def __init__(self, channel, stats=None):
        super().__init__(channel)
        self.stats, self.dropped = stats, 0

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # A slow disk loses lines rather than holding up requests.
            self.dropped += 1
            if self.stats is not None:
                self.stats.records_dropped += 1


class FileSink:
    """Request records as JSON lines in a rotating file, written by a background thread."""

    def __init__(self, path, max_bytes, stats=None):
        self.queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.handler = DroppingQueueHandler(self.queue, stats)
        # Not registered with logging: nothing else can reach or reconfigure it.
        self.logger = logging.Logger("openviking_gateway.requests", logging.INFO)
        self.logger.propagate = False
        self.logger.addHandler(self.handler)
        self.listener = _Listener(
            self.queue, rotating_file(path, max_bytes, logging.Formatter("%(message)s"))
        )
        self.listener.start()

    @property
    def dropped(self):
        return self.handler.dropped

    def write(self, value):
        self.logger.info(orjson.dumps(value).decode())

    async def log(self, account, value, user_id=None):
        """Same line as ``stdout_log``."""
        self.write({"time": time.time(), "account": account, "user_id": user_id, **value})

    def close(self):
        """Write what is queued, then close the file."""
        self.listener.stop()
        for handler in self.listener.handlers:
            handler.close()


class Stats:
    """In-process counters behind the snapshot line; the interval ones reset with each line."""

    def __init__(self):
        self.started = self.since = time.monotonic()
        self.inflight_requests = self.inflight_streams = 0
        self.reset()

    def reset(self):
        self.requests = self.rejected_overload = self.store_errors = 0
        self.records_unlogged = self.records_failed = self.records_dropped = 0
        # Finished turns written to OpenViking, and ones whose write failed.
        self.capture_delivered = self.capture_failed = 0
        self.errors, self.degradations, self.stream_errors = Counter(), Counter(), Counter()

    def recorded(self, metrics):
        """Count one written request record."""
        self.requests += 1
        if reason := metrics.get("error_reason"):
            self.errors[reason] += 1
        self.degradations.update(metrics.get("degradations", []))
        if stream_error := metrics.get("stream_error"):
            self.stream_errors[stream_error] += 1
        # memory_store_failure is the kernel's catch-all, most often a store error.
        if reason == "store_unavailable" or "memory_store_failure" in metrics.get(
            "degradations", []
        ):
            self.store_errors += 1

    async def snapshot(self, app) -> dict:
        """The A.7 line for the interval since the previous one; resets the interval counts."""
        now = time.monotonic()
        line = {
            "event": "snapshot",
            "schema": 1,
            "time": time.time(),
            "interval_s": round(now - self.since, 3),
            "uptime_s": round(now - self.started, 3),
            "version": package_version(),
            "inflight_requests": self.inflight_requests,
            "inflight_streams": self.inflight_streams,
            "requests": self.requests,
            "errors": dict(self.errors),
            "degradations": dict(self.degradations),
            "stream_errors": dict(self.stream_errors),
            "rejected_overload": self.rejected_overload,
            "records_unlogged": self.records_unlogged,
            "records_failed": self.records_failed,
            "records_dropped": self.records_dropped,
            "store_errors": self.store_errors,
        }
        delivered, failed = self.capture_delivered, self.capture_failed
        self.since = now
        self.reset()
        state = app.state
        health = getattr(state, "health", {}) or {}
        breaker = getattr(getattr(state, "viking", None), "breaker", None)
        line["openviking"] = {
            "state": health.get("status", "starting"),
            "reason": health.get("reason", ""),
            "open_circuits": breaker.open_count() if breaker else 0,
        }
        line["capture"] = {"delivered": delivered, "failed": failed}
        return line


def package_version():
    try:
        return version("openviking")
    except PackageNotFoundError:
        return "unknown"


def write_snapshot(app, line):
    """Snapshot lines go where request records go; the store keeps none, so they are logged."""
    sink = app.state.config.request_log_sink
    if sink == "file":
        app.state.file_sink.write(line)
    elif sink == "stdout":
        sys.stdout.buffer.write(orjson.dumps(line) + b"\n")
        sys.stdout.buffer.flush()
    else:
        text = orjson.dumps(line).decode()
        if snapshot_logger.hasHandlers():
            snapshot_logger.info(text)
        else:
            # logging.lastResort drops INFO; with no handler configured the line goes to stderr.
            sys.stderr.write(text + "\n")
            sys.stderr.flush()


async def snapshot_loop(app, interval, sleep=asyncio.sleep):
    while True:
        await sleep(interval)
        try:
            write_snapshot(app, await app.state.stats.snapshot(app))
        except Exception:
            logger.info("gateway snapshot failed; retrying next interval", extra={"degraded": True})


class JsonQueueHandler(DroppingQueueHandler):
    """Format application records in the caller's task, preserving Gateway context."""

    def prepare(self, record):
        call = CALL.get()
        record = logging.makeLogRecord(record.__dict__.copy())
        if call and call.request_id:
            record.request_id = call.request_id
        TraceContextFilter().filter(record)
        return super().prepare(record)


def configure_logging(path=None, max_bytes=LOG_MAX_BYTES):
    """Own standalone application output; request-record sinks retain their schema."""
    channel = queue.Queue(maxsize=QUEUE_SIZE)
    handler = JsonQueueHandler(channel)
    handler.setLevel(logging.INFO)
    handler.setFormatter(CommonLogFormatter())
    output = (
        rotating_file(path, max_bytes, logging.Formatter("%(message)s"))
        if path
        else logging.StreamHandler(sys.stderr)
    )
    listener = _Listener(channel, output)
    handler.listener = listener
    root = logging.getLogger()
    for old in root.handlers[:]:
        previous = getattr(old, "listener", None)
        if isinstance(previous, _Listener):
            previous.stop()
            for destination in previous.handlers:
                destination.close()
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        component = logging.getLogger(name)
        component.handlers.clear()
        component.propagate = True
    listener.start()
    atexit.register(listener.stop)
    return listener
