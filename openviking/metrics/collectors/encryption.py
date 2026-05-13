# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: EncryptionCollector.

This collector is fed by encryption DataSources (and by crypto code paths emitting those events).
It exports operational metrics for:
- encrypt/decrypt operation count and latency
- processed bytes and payload size histogram
- auth-failure count
- key derivation/load/cache hit/miss signals

All labels are intentionally low-cardinality:
- operation/status/provider/key_version are expected to be stable and bounded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class EncryptionCollector(EventMetricCollector):
    """
    Translate encryption lifecycle events into low-cardinality operational metrics.

    The collector receives normalized events from crypto instrumentation and expands them into
    counters and histograms that describe operation throughput, latency, byte volume, and
    key-management health signals without exposing high-cardinality payload data.
    """

    DOMAIN: ClassVar[str] = "encryption"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_operations_total
    # e.g.: openviking_encryption_operations_total
    OPERATIONS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "operations", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_duration_seconds
    # e.g.: openviking_encryption_duration_seconds
    DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_bytes_total
    # e.g.: openviking_encryption_bytes_total
    BYTES_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "bytes", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_payload_size_bytes
    # e.g.: openviking_encryption_payload_size_bytes
    PAYLOAD_SIZE_BYTES: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "payload_size", unit="bytes"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_auth_failed_total
    # e.g.: openviking_encryption_auth_failed_total
    AUTH_FAILED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "auth_failed", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_key_derivation_total
    # e.g.: openviking_encryption_key_derivation_total
    KEY_DERIVATION_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "key_derivation", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_key_derivation_duration_seconds
    # e.g.: openviking_encryption_key_derivation_duration_seconds
    KEY_DERIVATION_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "key_derivation_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_key_load_duration_seconds
    # e.g.: openviking_encryption_key_load_duration_seconds
    KEY_LOAD_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "key_load_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_key_cache_hits_total
    # e.g.: openviking_encryption_key_cache_hits_total
    KEY_CACHE_HITS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "key_cache_hits", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_key_cache_misses_total
    # e.g.: openviking_encryption_key_cache_misses_total
    KEY_CACHE_MISSES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "key_cache_misses", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_key_version_usage_total
    # e.g.: openviking_encryption_key_version_usage_total
    KEY_VERSION_USAGE_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "key_version_usage", unit="total"
    )

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "encryption.operation",
            "encryption.bytes",
            "encryption.payload_size",
            "encryption.auth_failed",
            "encryption.key_derivation",
            "encryption.key_load",
            "encryption.key_cache_hit",
            "encryption.key_cache_miss",
            "encryption.key_version_usage",
        }
    )

    def collect(self, registry=None) -> None:
        """Implement the collector interface as a no-op because encryption metrics are event-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Dispatch one normalized encryption event to the matching metric writer helper.

        Each supported event has a fixed payload shape defined by the datasource layer, so this
        hook only performs lightweight coercion before updating the registry.
        """
        if event_name == "encryption.operation":
            self.record_operation(
                registry,
                operation=str(payload["operation"]),
                status=str(payload["status"]),
                duration_seconds=float(payload["duration_seconds"]),
            )
            return
        if event_name == "encryption.bytes":
            self.record_bytes(
                registry,
                operation=str(payload["operation"]),
                size_bytes=int(payload["size_bytes"]),
            )
            return
        if event_name == "encryption.payload_size":
            self.record_payload_size(
                registry,
                operation=str(payload["operation"]),
                size_bytes=int(payload["size_bytes"]),
            )
            return
        if event_name == "encryption.auth_failed":
            self.record_auth_failed(registry)
            return
        if event_name == "encryption.key_derivation":
            self.record_key_derivation(
                registry,
                status=str(payload["status"]),
                duration_seconds=float(payload["duration_seconds"]),
            )
            return
        if event_name == "encryption.key_load":
            self.record_key_load(
                registry,
                status=str(payload["status"]),
                provider=str(payload["provider"]),
                duration_seconds=float(payload["duration_seconds"]),
            )
            return
        if event_name == "encryption.key_cache_hit":
            self.record_key_cache_hit(registry, provider=str(payload["provider"]))
            return
        if event_name == "encryption.key_cache_miss":
            self.record_key_cache_miss(registry, provider=str(payload["provider"]))
            return
        if event_name == "encryption.key_version_usage":
            self.record_key_version_usage(
                registry,
                key_version=str(payload["key_version"]),
            )

    def record_operation(
        self, registry, *, operation: str, status: str, duration_seconds: float
    ) -> None:
        """
        Record one encryption or decryption attempt and its observed latency.

        Both the counter and histogram share the same `(operation, status)` label set so success
        and error paths can be aggregated consistently in PromQL.
        """
        labels = {"operation": str(operation), "status": str(status)}
        registry.inc_counter(
            self.OPERATIONS_TOTAL,
            labels=labels,
            label_names=("operation", "status"),
        )
        registry.observe_histogram(
            self.DURATION_SECONDS,
            float(duration_seconds),
            labels=labels,
            label_names=("operation", "status"),
        )

    def record_bytes(self, registry, *, operation: str, size_bytes: int) -> None:
        """Increment the processed-bytes counter when the observed payload size is positive."""
        if size_bytes <= 0:
            return
        registry.inc_counter(
            self.BYTES_TOTAL,
            labels={"operation": str(operation)},
            label_names=("operation",),
            amount=int(size_bytes),
        )

    def record_payload_size(self, registry, *, operation: str, size_bytes: int) -> None:
        """Observe the payload-size distribution for one logical encryption operation."""
        if size_bytes < 0:
            return
        registry.observe_histogram(
            self.PAYLOAD_SIZE_BYTES,
            float(size_bytes),
            labels={"operation": str(operation)},
            label_names=("operation",),
            buckets=(64, 256, 1024, 4096, 16384, 65536, 262144, 1048576),
        )

    def record_auth_failed(self, registry) -> None:
        """Increment the authentication-failure counter for failed payload verification."""
        registry.inc_counter(self.AUTH_FAILED_TOTAL)

    def record_key_derivation(self, registry, *, status: str, duration_seconds: float) -> None:
        """Record the outcome and latency of one key-derivation attempt."""
        registry.inc_counter(
            self.KEY_DERIVATION_TOTAL,
            labels={"status": str(status)},
            label_names=("status",),
        )
        registry.observe_histogram(
            self.KEY_DERIVATION_DURATION_SECONDS,
            float(duration_seconds),
            labels={"status": str(status)},
            label_names=("status",),
        )

    def record_key_load(
        self, registry, *, status: str, provider: str, duration_seconds: float
    ) -> None:
        """Record key-load latency grouped by provider and final status."""
        registry.observe_histogram(
            self.KEY_LOAD_DURATION_SECONDS,
            float(duration_seconds),
            labels={"status": str(status), "provider": str(provider)},
            label_names=("status", "provider"),
        )

    def record_key_cache_hit(self, registry, *, provider: str) -> None:
        """Increment the key-cache hit counter for one normalized provider identifier."""
        registry.inc_counter(
            self.KEY_CACHE_HITS_TOTAL,
            labels={"provider": str(provider)},
            label_names=("provider",),
        )

    def record_key_cache_miss(self, registry, *, provider: str) -> None:
        """Increment the key-cache miss counter for one normalized provider identifier."""
        registry.inc_counter(
            self.KEY_CACHE_MISSES_TOTAL,
            labels={"provider": str(provider)},
            label_names=("provider",),
        )

    def record_key_version_usage(self, registry, *, key_version: str) -> None:
        """Increment the usage counter for the normalized key-version label."""
        registry.inc_counter(
            self.KEY_VERSION_USAGE_TOTAL,
            labels={"key_version": str(key_version)},
            label_names=("key_version",),
        )
