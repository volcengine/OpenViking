# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Model usage related DataSources.

This file provides two categories of DataSource APIs:
1) DomainStats (pull): `ModelUsageDataSource.read_model_usage()` reads aggregated cumulative
   usage from in-process model instances / shared token trackers. This is used by
   `ModelUsageCollector` which converts cumulative stats into Prometheus Counters via deltas.
2) Event (push): `VLMEventDataSource`, `EmbeddingEventDataSource`, and
   `RerankEventDataSource` emit per-call events.
   These events are routed to Event collectors via `try_dispatch_event`.

Note: DataSources are not allowed to write into MetricRegistry directly in this architecture.
They only emit events or expose read APIs. Collectors are the only writers.
"""

from __future__ import annotations

from typing import Any, Callable

from openviking.metrics.core.base import ReadEnvelope

from .base import DomainStatsMetricDataSource, EventMetricDataSource


class ModelUsageDataSource(DomainStatsMetricDataSource):
    """
    DomainStats source for aggregated model usage.

    The return value is intentionally aligned with `TokenUsageTracker.to_dict()`:
    - usage_by_model[model_name].usage_by_provider[provider] contains cumulative counters:
      prompt_tokens, completion_tokens, total_tokens, call_count
    """

    def __init__(self, *, config_provider: Callable[[], Any], service: Any = None) -> None:
        """
        Args:
            config_provider: Fallback provider to obtain OpenVikingConfig when `service` is absent.
            service: Optional server service instance. When provided, we prefer its in-memory
                config to avoid reloading configuration from disk.
        """
        self._config_provider = config_provider
        self._service = service

    def read_model_usage(self) -> ReadEnvelope[dict[str, dict]]:
        """
        Read cumulative model usage for configured model types.

        Returns:
            A dict keyed by model_type: "vlm" / "embedding" / "rerank".
            Each value contains:
            - "available": whether fresh usage data for that type was obtained
            - "usage_by_model": normalized cumulative usage payload, empty when unavailable

        Notes:
            - This method is best-effort: it skips types that are not configured/available.
            - It must not create additional long-lived clients except where unavoidable.
              (Rerank currently uses RerankClient.from_config to access the shared token tracker.)
        """
        config_env = self.safe_read(
            lambda: getattr(self._service, "_config", None) or self._config_provider(),
            default=None,
        )
        if not config_env.ok or config_env.value is None:
            return ReadEnvelope(
                ok=False,
                value={},
                error_type=config_env.error_type,
                error_message=config_env.error_message,
            )
        config = config_env.value

        result: dict[str, dict[str, object]] = {
            "vlm": {"available": False, "usage_by_model": {}},
            "embedding": {"available": False, "usage_by_model": {}},
            "rerank": {"available": False, "usage_by_model": {}},
        }

        try:
            vlm = config.vlm.get_vlm_instance()
            result["vlm"] = {
                "available": True,
                "usage_by_model": _extract_usage_by_model(vlm.get_token_usage(), self),
            }
        except Exception:
            pass

        try:
            embedder = config.embedding.get_embedder()
            result["embedding"] = {
                "available": True,
                "usage_by_model": _extract_usage_by_model(embedder.get_token_usage(), self),
            }
        except Exception:
            pass

        try:
            rerank_cfg = getattr(config, "rerank", None)
            if rerank_cfg is not None and rerank_cfg.is_available():
                from openviking.models.rerank.base import get_shared_rerank_token_usage

                result["rerank"] = {
                    "available": True,
                    "usage_by_model": _extract_usage_by_model(
                        get_shared_rerank_token_usage(),
                        self,
                    ),
                }
        except Exception:
            pass

        return ReadEnvelope(ok=True, value=result)


class VLMEventDataSource(EventMetricDataSource):
    """
    Event datasource for per-call VLM usage events.

    The payload emitted here is intentionally narrow and collector-facing: model identity, token
    counts, latency, and optional account context.
    """

    @staticmethod
    def record_call(
        *,
        provider: str,
        model_name: str,
        duration_seconds: float,
        prompt_tokens: int,
        completion_tokens: int,
        account_id: str | None = None,
    ) -> None:
        """
        Emit one VLM call event with model identity, latency, token usage, and account context.

        The caller is expected to provide already-normalized provider/model identifiers and the
        final token counts that should be reflected in Prometheus usage metrics.
        """
        EventMetricDataSource._emit(
            "vlm.call",
            {
                "provider": str(provider),
                "model_name": str(model_name),
                "duration_seconds": float(duration_seconds),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "account_id": None if account_id is None else str(account_id),
            },
        )


class EmbeddingEventDataSource(EventMetricDataSource):
    """
    Event datasource for embedding request outcomes.

    Success and error outcomes are modeled as separate event names so collectors can produce the
    appropriate request, latency, and error-code series without inspecting exception objects.
    """

    @staticmethod
    def record_call(
        *,
        provider: str,
        model_name: str,
        duration_seconds: float,
        prompt_tokens: int,
        completion_tokens: int,
        account_id: str | None = None,
    ) -> None:
        """Emit one embedding provider call with tokens, latency, and optional account context."""
        EventMetricDataSource._emit(
            "embedding.call",
            {
                "provider": str(provider),
                "model_name": str(model_name),
                "duration_seconds": float(duration_seconds),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "account_id": None if account_id is None else str(account_id),
            },
        )

    @staticmethod
    def record_success(*, latency_seconds: float, account_id: str | None = None) -> None:
        """
        Emit an embedding success event with the observed end-to-end latency.

        The latency is expected to cover the full embedding workflow segment that the caller wants
        reflected in metrics, not just the raw provider round-trip time.
        """
        EventMetricDataSource._emit(
            "embedding.success",
            {
                "latency_seconds": float(latency_seconds),
                "account_id": None if account_id is None else str(account_id),
            },
        )

    @staticmethod
    def record_error(*, error_code: str, account_id: str | None = None) -> None:
        """
        Emit an embedding error event with a normalized error code label.

        Callers should translate provider- or worker-specific failures into bounded error codes
        before invoking this datasource.
        """
        EventMetricDataSource._emit(
            "embedding.error",
            {
                "error_code": str(error_code or "unknown"),
                "account_id": None if account_id is None else str(account_id),
            },
        )


class RerankEventDataSource(EventMetricDataSource):
    """Event datasource for per-call rerank usage events."""

    @staticmethod
    def record_call(
        *,
        provider: str,
        model_name: str,
        duration_seconds: float,
        prompt_tokens: int,
        completion_tokens: int,
        account_id: str | None = None,
    ) -> None:
        """Emit one rerank provider call with tokens, latency, and optional account context."""
        EventMetricDataSource._emit(
            "rerank.call",
            {
                "provider": str(provider),
                "model_name": str(model_name),
                "duration_seconds": float(duration_seconds),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "account_id": None if account_id is None else str(account_id),
            },
        )


def _extract_usage_by_model(token_usage: dict, datasource: DomainStatsMetricDataSource) -> dict:
    """
    Extract and normalize a `usage_by_model` mapping from TokenUsageTracker-like payloads.

    The helper keeps only the counters required by `ModelUsageCollector` and normalizes model and
    provider keys into stable non-empty strings.
    """
    token_usage = datasource.as_dict(token_usage)
    usage_by_model = datasource.as_dict(token_usage.get("usage_by_model"))
    normalized: dict[str, dict] = {}
    for model_name, model_payload in usage_by_model.items():
        model_key = datasource.normalize_str(model_name)
        normalized_model = datasource.as_dict(model_payload)
        usage_by_provider = datasource.as_dict(normalized_model.get("usage_by_provider"))
        normalized_providers: dict[str, dict] = {}
        for provider_name, provider_payload in usage_by_provider.items():
            provider_key = datasource.normalize_str(provider_name)
            provider_dict = datasource.as_dict(provider_payload)
            normalized_providers[provider_key] = {
                "prompt_tokens": datasource.as_int(provider_dict.get("prompt_tokens"), default=0),
                "completion_tokens": datasource.as_int(
                    provider_dict.get("completion_tokens"), default=0
                ),
                "total_tokens": datasource.as_int(provider_dict.get("total_tokens"), default=0),
                "call_count": datasource.as_int(provider_dict.get("call_count"), default=0),
            }
        normalized[model_key] = {
            "usage_by_provider": normalized_providers,
        }
    return normalized
