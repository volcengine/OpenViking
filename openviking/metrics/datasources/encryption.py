# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Encryption metrics DataSources.

The crypto subsystem emits best-effort metrics through these DataSource APIs.
All functions here either:
- emit in-process events (handled by EncryptionCollector), or
- expose readiness probe state (handled by EncryptionProbeCollector).

Instrumentation policy:
- Must never raise and break encryption logic.
- Labels must remain low-cardinality (operation/status/provider/key_version).
"""

from __future__ import annotations

from openviking.metrics.core.base import ReadEnvelope

from .base import EventMetricDataSource, ProbeMetricDataSource


class EncryptionEventDataSource(EventMetricDataSource):
    """Emit low-cardinality encryption runtime events for `EncryptionCollector`."""

    @staticmethod
    def record_operation(*, operation: str, status: str, duration_seconds: float) -> None:
        """Emit a timed encryption/decryption operation outcome."""
        EventMetricDataSource._emit(
            "encryption.operation",
            {
                "operation": str(operation),
                "status": str(status),
                "duration_seconds": float(duration_seconds),
            },
        )

    @staticmethod
    def record_bytes(*, operation: str, size_bytes: int) -> None:
        """Emit the number of bytes processed by one encryption operation when positive."""
        if size_bytes <= 0:
            return
        EventMetricDataSource._emit(
            "encryption.bytes",
            {
                "operation": str(operation),
                "size_bytes": int(size_bytes),
            },
        )

    @staticmethod
    def record_payload_size(*, operation: str, size_bytes: int) -> None:
        """Emit the logical payload size associated with one encryption operation."""
        if size_bytes < 0:
            return
        EventMetricDataSource._emit(
            "encryption.payload_size",
            {
                "operation": str(operation),
                "size_bytes": int(size_bytes),
            },
        )

    @staticmethod
    def record_auth_failed() -> None:
        """Emit an authentication-failure event for encryption payload verification."""
        EventMetricDataSource._emit("encryption.auth_failed", {})

    @staticmethod
    def record_key_derivation(*, status: str, duration_seconds: float) -> None:
        """Emit the outcome and latency of a key-derivation attempt."""
        EventMetricDataSource._emit(
            "encryption.key_derivation",
            {"status": str(status), "duration_seconds": float(duration_seconds)},
        )

    @staticmethod
    def record_key_load(*, status: str, provider: str, duration_seconds: float) -> None:
        """Emit the outcome and latency of loading a key from the configured provider."""
        EventMetricDataSource._emit(
            "encryption.key_load",
            {
                "status": str(status),
                "provider": str(provider),
                "duration_seconds": float(duration_seconds),
            },
        )

    @staticmethod
    def record_key_cache_hit(*, provider: str) -> None:
        """Emit a key-cache hit event for the given provider."""
        EventMetricDataSource._emit("encryption.key_cache_hit", {"provider": str(provider)})

    @staticmethod
    def record_key_cache_miss(*, provider: str) -> None:
        """Emit a key-cache miss event for the given provider."""
        EventMetricDataSource._emit("encryption.key_cache_miss", {"provider": str(provider)})

    @staticmethod
    def record_key_version_usage(*, key_version: str) -> None:
        """Emit the normalized key version that was used for an encryption operation."""
        EventMetricDataSource._emit(
            "encryption.key_version_usage", {"key_version": str(key_version)}
        )


class EncryptionProbeDataSource(ProbeMetricDataSource):
    """Read coarse readiness state for the encryption subsystem and configured provider."""

    def __init__(self, *, config_provider) -> None:
        """Store the config provider used to resolve encryption bootstrap configuration."""
        self._config_provider = config_provider

    def read_probe_state(self) -> ReadEnvelope[tuple[bool, str]]:
        """
        Check whether encryption bootstrap succeeds and report the configured provider name.

        Returns:
            A tuple of `(ok_component, provider)` where `ok_component` indicates whether the
            encryption subsystem could be initialized successfully.
        """
        from openviking.crypto.config import bootstrap_encryption

        def _read() -> tuple[bool, str]:
            cfg = self._config_provider()
            provider = str(getattr(getattr(cfg, "encryption", None), "provider", "") or "unknown")
            bootstrap_encryption()
            return True, provider

        default_provider = "unknown"
        try:
            cfg = self._config_provider()
            default_provider = str(
                getattr(getattr(cfg, "encryption", None), "provider", "") or "unknown"
            )
        except Exception:
            default_provider = "unknown"

        return self.safe_value_probe(_read, default=(False, default_provider))
