"""Bounded event exporter with a single background Kafka worker."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)


def _warn_count(message: str, count: int) -> None:
    """Keep failures visible without logging every event during an outage."""
    if count & (count - 1) == 0:
        logger.warning("%s (count=%d)", message, count)


class KafkaEventReporter:
    def __init__(self, config: dict, *, producer=None):
        kafka = config.get("kafka", {})
        options = config.get("telemetry", {})
        capacity = int(options.get("queue_capacity", 1024))
        if capacity <= 0:
            raise ValueError("telemetry.queue_capacity must be a positive integer")
        self.topic = kafka.get("topic", "")
        if not self.topic:
            raise ValueError("kafka.topic is required")
        if producer is None:
            from kafka import KafkaProducer

            properties = kafka.get("properties", {})
            kwargs = {
                "bootstrap_servers": kafka["bootstrap_servers"].split(","),
                "security_protocol": kafka.get(
                    "security_protocol", properties.get("security.protocol", "PLAINTEXT")
                ),
                "acks": "all",
                "retries": 2,
                "max_block_ms": 5000,
                "request_timeout_ms": 10000,
                "delivery_timeout_ms": 20000,
                "allow_auto_create_topics": False,
            }
            if kwargs["security_protocol"].startswith("SASL_"):
                kwargs.update(
                    sasl_mechanism=properties.get("sasl.mechanism", "PLAIN"),
                    sasl_plain_username=kafka["username"],
                    sasl_plain_password=kafka["password"],
                )
            if kafka.get("proxy_url"):
                kwargs["proxy_url"] = kafka["proxy_url"]
            self._producer_factory = lambda: KafkaProducer(**kwargs)
        self.producer = producer
        self.queue: queue.Queue[tuple[bytes, bytes]] = queue.Queue(maxsize=capacity)
        self.sent = self.failed = self.dropped = 0
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="ov-event-exporter", daemon=True)
        self._thread.start()

    def report(self, event: dict) -> bool:
        try:
            payload = json.dumps(
                event, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
            if len(payload) > 1024 * 1024:
                raise ValueError("event too large")
            key = "|".join(
                str(event.get(k) or "") for k in ("account_id", "resource_id", "ov_account_id")
            ).encode()
            with self._lock:
                if self._closed:
                    raise ValueError("reporter closed")
                self.queue.put_nowait((key, payload))
            return True
        except (ValueError, TypeError, queue.Full):
            with self._lock:
                self.dropped += 1
                dropped = self.dropped
            _warn_count("Event exporter dropped events", dropped)
            return False

    def _run(self):
        try:
            while True:
                try:
                    item = self.queue.get(timeout=0.1)
                except queue.Empty:
                    with self._lock:
                        finished = self._closed
                    if finished:
                        return
                    continue
                try:
                    key, payload = item
                    if self.producer is None:
                        self.producer = self._producer_factory()
                    self.producer.send(self.topic, key=key, value=payload).get(timeout=10)
                    with self._lock:
                        self.sent += 1
                except Exception:
                    with self._lock:
                        self.failed += 1
                        failed = self.failed
                    _warn_count("Event exporter delivery failed", failed)
                finally:
                    self.queue.task_done()
        finally:
            try:
                if self.producer is not None:
                    self.producer.close(timeout=5)
            except Exception:
                with self._lock:
                    self.failed += 1

    def flush(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self.queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                raise TimeoutError("event exporter flush timed out")
            time.sleep(0.01)
        if self.failed:
            raise RuntimeError("event delivery failed; inspect exporter counters")

    def close(self, timeout=5.0):
        with self._lock:
            self._closed = True
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("event exporter shutdown still draining")
        if self.failed:
            raise RuntimeError("event exporter closed with delivery failures")
