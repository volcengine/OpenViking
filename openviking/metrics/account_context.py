# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Request-scoped account context for metrics label injection.

This module keeps metrics-specific request context separate from business-layer request
objects so collectors can resolve `account_id` without depending on FastAPI internals.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricAccountContext:
    """
    Store request-scoped account information used during metrics label resolution.

    The context currently keeps the HTTP-derived account identifier that collectors and
    account-dimension policy code can consult without reaching back into framework-specific
    request objects.
    """

    http_account_id: str | None = None


_CURRENT_METRIC_ACCOUNT_CONTEXT: contextvars.ContextVar[MetricAccountContext | None] = (
    contextvars.ContextVar(
        "openviking_metric_account_context",
        default=None,
    )
)


def get_metric_account_context() -> MetricAccountContext:
    """
    Return the metrics account context bound to the current execution flow.

    When no context has been bound yet, the function returns an empty `MetricAccountContext`
    instance so callers can treat the result as a stable object instead of handling `None`.
    """
    context = _CURRENT_METRIC_ACCOUNT_CONTEXT.get()
    if context is None:
        return MetricAccountContext()
    return context


def bind_metric_account_context(
    *, account_id: str | None
) -> contextvars.Token[MetricAccountContext | None]:
    """
    Bind a new metrics account context and return the token needed to restore the prior one.

    This helper is intended for request or worker entry points that need scoped lifetime
    management through `contextvars.Token`, typically via `try/finally`.
    """
    return _CURRENT_METRIC_ACCOUNT_CONTEXT.set(MetricAccountContext(http_account_id=account_id))


def set_metric_account_context(*, account_id: str | None) -> None:
    """
    Overwrite the current metrics account context for the active execution flow.

    Unlike `bind_metric_account_context`, this helper does not provide a restoration token and
    is therefore best suited to one-way updates where the caller owns the full scope lifetime.
    """
    _CURRENT_METRIC_ACCOUNT_CONTEXT.set(MetricAccountContext(http_account_id=account_id))


def reset_metric_account_context(token: contextvars.Token[MetricAccountContext | None]) -> None:
    """
    Restore the previous metrics account context captured by `bind_metric_account_context`.

    Callers should use the exact token returned by the matching bind operation so nested context
    updates unwind in the correct order.
    """
    _CURRENT_METRIC_ACCOUNT_CONTEXT.reset(token)
