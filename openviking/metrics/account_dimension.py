# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Account-dimension policy and runtime configuration for metrics.

This module keeps account label resolution outside of the registry so label injection remains a
collector-side concern while still being configurable process-wide.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from openviking.observability.context import get_root_observability_context

UNKNOWN_ACCOUNT_ID = "__unknown__"
OVERFLOW_ACCOUNT_ID = "__overflow__"
ACCOUNT_DIMENSION_SUPPORTED_METRICS = frozenset(
    {
        "openviking_embedding_requests_total",
        "openviking_embedding_latency_seconds",
        "openviking_embedding_errors_total",
        "openviking_embedding_calls_total",
        "openviking_embedding_call_duration_seconds",
        "openviking_embedding_tokens_input_total",
        "openviking_embedding_tokens_output_total",
        "openviking_embedding_tokens_total",
        "openviking_http_requests_total",
        "openviking_http_request_duration_seconds",
        "openviking_http_inflight_requests",
        "openviking_resource_stage_total",
        "openviking_resource_stage_duration_seconds",
        "openviking_resource_wait_duration_seconds",
        "openviking_retrieval_requests_total",
        "openviking_retrieval_results_total",
        "openviking_retrieval_zero_result_total",
        "openviking_retrieval_latency_seconds",
        "openviking_retrieval_rerank_used_total",
        "openviking_retrieval_rerank_fallback_total",
        "openviking_session_lifecycle_total",
        "openviking_session_contexts_used_total",
        "openviking_session_archive_total",
        "openviking_operation_requests_total",
        "openviking_operation_duration_seconds",
        "openviking_operation_tokens_total",
        "openviking_vlm_calls_total",
        "openviking_vlm_call_duration_seconds",
        "openviking_vlm_tokens_input_total",
        "openviking_vlm_tokens_output_total",
        "openviking_vlm_tokens_total",
        "openviking_rerank_calls_total",
        "openviking_rerank_call_duration_seconds",
        "openviking_rerank_tokens_input_total",
        "openviking_rerank_tokens_output_total",
        "openviking_rerank_tokens_total",
    }
)


@dataclass(frozen=True, slots=True)
class MetricAccountDimensionConfig:
    """
    Describe the process-wide settings that govern `account_id` label injection.

    The config is intentionally narrow: enablement, per-metric allowlisting, and the active
    account cap that protects Prometheus from unbounded tenant cardinality.
    """

    enabled: bool = False
    max_active_accounts: int = 0
    metric_allowlist: frozenset[str] = frozenset()


class MetricAccountContextResolver:
    """
    Resolve candidate account identifiers from the supported metrics context sources.

    Resolution follows the project-wide source priority so all collectors interpret account
    provenance consistently.
    """

    def resolve(
        self,
        *,
        explicit_account_id: str | None = None,
        owner_account_id: str | None = None,
    ) -> str | None:
        """
        Resolve one account identifier in priority order: explicit > HTTP context > task owner.

        The method normalizes blank values away and returns `None` when no source can supply a
        stable account id for the current metric write.
        """
        if explicit_account_id and str(explicit_account_id).strip():
            return str(explicit_account_id).strip()

        root_context = get_root_observability_context()
        http_account_id = root_context.account_id if root_context is not None else None
        if http_account_id and str(http_account_id).strip():
            return str(http_account_id).strip()

        if owner_account_id and str(owner_account_id).strip():
            return str(owner_account_id).strip()
        return None


class MetricAccountDimensionPolicy:
    """
    Decide the final `account_id` label value for an individual metric write.

    The policy layer is responsible for the allowlist gate and active-account limiting. It does
    not discover account ids itself; it only decides whether a resolved candidate becomes a real
    tenant label, `__unknown__`, or `__overflow__`.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        metric_allowlist: set[str] | frozenset[str],
        max_active_accounts: int,
    ) -> None:
        """
        Initialize policy state for allowlist filtering and active-account limiting.

        The active-account set is held in-memory and guarded by a lock because collectors may
        resolve account labels concurrently from request threads and exporter refresh paths.
        """
        self._enabled = bool(enabled)
        exact: set[str] = set()
        prefixes: set[str] = set()
        for item in metric_allowlist:
            normalized = str(item).strip()
            if not normalized:
                continue
            if normalized.endswith("*"):
                # Support a limited wildcard syntax: trailing '*' means prefix match.
                prefix = normalized[:-1].strip()
                if prefix:
                    prefixes.add(prefix)
                continue
            exact.add(normalized)
        self._metric_allowlist_exact = frozenset(exact)
        self._metric_allowlist_prefixes = tuple(sorted(prefixes))
        self._max_active_accounts = max(0, int(max_active_accounts))
        self._lock = threading.Lock()
        self._active_accounts: set[str] = set()

    def _is_metric_allowlisted(self, metric_name: str) -> bool:
        """Return whether the metric name passes the allowlist gate (exact or prefix '*')."""
        name = str(metric_name)
        if name in self._metric_allowlist_exact:
            return True
        for prefix in self._metric_allowlist_prefixes:
            if name.startswith(prefix):
                return True
        return False

    def resolve(self, *, metric_name: str, account_id: str | None) -> str:
        """
        Resolve the stored label value as a real id, `__unknown__`, or `__overflow__`.

        A metric only receives a real tenant id when account-dimension support is enabled, the
        metric name is explicitly allowlisted, and the account passes the active-account cap.
        """
        if not self._enabled:
            return UNKNOWN_ACCOUNT_ID
        if not self._is_metric_allowlisted(metric_name):
            return UNKNOWN_ACCOUNT_ID

        normalized = str(account_id or "").strip()
        if not normalized:
            return UNKNOWN_ACCOUNT_ID

        with self._lock:
            if normalized in self._active_accounts:
                return normalized
            if (
                self._max_active_accounts > 0
                and len(self._active_accounts) >= self._max_active_accounts
            ):
                return OVERFLOW_ACCOUNT_ID
            self._active_accounts.add(normalized)
            return normalized


_RUNTIME_LOCK = threading.Lock()
_RUNTIME_POLICY = MetricAccountDimensionPolicy(
    enabled=False,
    metric_allowlist=set(),
    max_active_accounts=0,
)
_RUNTIME_RESOLVER = MetricAccountContextResolver()


def configure_metric_account_dimension(
    *,
    enabled: bool | None = None,
    metric_allowlist: set[str] | list[str] | tuple[str, ...] | None = None,
    max_active_accounts: int | None = None,
    policy: MetricAccountDimensionPolicy | None = None,
    resolver: MetricAccountContextResolver | None = None,
) -> None:
    """
    Configure the process-global account-dimension runtime used by collector write helpers.

    Callers may replace the policy and resolver explicitly for tests, or provide primitive
    configuration fields that are converted into the default runtime objects.
    """
    global _RUNTIME_POLICY, _RUNTIME_RESOLVER
    with _RUNTIME_LOCK:
        if policy is not None:
            _RUNTIME_POLICY = policy
        else:
            _RUNTIME_POLICY = MetricAccountDimensionPolicy(
                enabled=bool(enabled),
                metric_allowlist=set(metric_allowlist or ()),
                max_active_accounts=int(max_active_accounts or 0),
            )
        if resolver is not None:
            _RUNTIME_RESOLVER = resolver


def reset_metric_account_dimension() -> None:
    """
    Reset the process-global account-dimension runtime to the disabled default state.

    This helper is mainly used by tests and shutdown paths that need deterministic process-wide
    cleanup between runs.
    """
    configure_metric_account_dimension(
        enabled=False,
        metric_allowlist=set(),
        max_active_accounts=0,
        resolver=MetricAccountContextResolver(),
    )


def resolve_metric_account_label(
    *,
    metric_name: str,
    explicit_account_id: str | None = None,
    owner_account_id: str | None = None,
) -> str:
    """
    Resolve the final `account_id` label for one metric write using the global runtime objects.

    The helper snapshots the current resolver and policy under the runtime lock, then performs
    the actual resolution outside the lock to keep the synchronized section small.
    """
    with _RUNTIME_LOCK:
        resolver = _RUNTIME_RESOLVER
        policy = _RUNTIME_POLICY
    return policy.resolve(
        metric_name=metric_name,
        account_id=resolver.resolve(
            explicit_account_id=explicit_account_id,
            owner_account_id=owner_account_id,
        ),
    )


def metric_supports_account_dimension(metric_name: str) -> bool:
    """
    Return whether a metric family participates in account-dimension label injection at all.

    This support set is narrower than the allowlist: unsupported metrics never receive an
    `account_id` label even if configuration would otherwise enable account-dimension writes.
    """
    return str(metric_name) in ACCOUNT_DIMENSION_SUPPORTED_METRICS
