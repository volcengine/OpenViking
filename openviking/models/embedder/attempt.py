# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observe existing embedding operations without adding retries or model calls.

An SDK may retry internally or fail before sending bytes. These observations
are embedding operations, not physical transport receipts or complete bills.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import uuid4

from openviking.models.model_error import ModelErrorKind, model_error_kind
from openviking.utils.model_retry import extract_metric_error_code


def token_count(value):
    """Only nonnegative integer provider values are token facts."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def embedding_usage(usage):
    """Embedding endpoints do not generate completion tokens.

    Missing input counts remain unknown. A reported total must not be turned
    into output tokens by subtracting a default input of zero.
    """

    def get(name):
        return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

    prompt = token_count(get("prompt_tokens"))
    if prompt is None:
        prompt = token_count(get("input_tokens"))
    return prompt, 0 if usage is not None else None


def execution_owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), id(task) if task is not None else None


@dataclass
class EmbeddingAttempt:
    backend: Any
    provider: str
    started: float = field(default_factory=monotonic)
    attempt_id: str = field(default_factory=lambda: "attempt_" + uuid4().hex)
    duration_seconds: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    outcome: str = "success"
    error_code: str = "OK"
    observation_scope: str = "embedding_operation"

    def fail(self, error):
        if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
            self.outcome, self.error_code = "cancelled", "cancelled"
        else:
            self.outcome = (
                "refused" if model_error_kind(error) == ModelErrorKind.CONTENT_SAFETY else "failed"
            )
            self.error_code = extract_metric_error_code(error)

    def settle(self):
        values = self.usage
        self.backend._record_token_usage(
            model_name=values.get("model_name", self.backend.model_name),
            provider=values.get("provider", self.provider),
            prompt_tokens=values.get("prompt_tokens"),
            completion_tokens=values.get("completion_tokens"),
            duration_seconds=self.duration_seconds,
            attempt_id=self.attempt_id,
            observation_scope=self.observation_scope,
            outcome=self.outcome,
            error_code=self.error_code,
            usage_quality=values.get("usage_quality", "unknown"),
            usage_provenance=values.get("usage_provenance", "unknown"),
        )


@dataclass
class EmbeddingScope:
    backend: Any
    provider: str
    owner: tuple[int, int | None] = field(default_factory=execution_owner)
    attempts: list[EmbeddingAttempt] = field(default_factory=list)
    explicit_requests: bool = False


_scopes: contextvars.ContextVar[tuple[EmbeddingScope, ...]] = contextvars.ContextVar(
    "embedding_observation_scopes", default=()
)


def current_scope(backend) -> EmbeddingScope | None:
    owner = execution_owner()
    return next(
        (
            scope
            for scope in reversed(_scopes.get())
            if scope.backend is backend and scope.owner == owner
        ),
        None,
    )


@contextmanager
def observe_request(backend, *, outer=False):
    """Start only after existing admission has granted execution capacity."""
    scope = current_scope(backend)
    attempt = (
        EmbeddingAttempt(backend, scope.provider)
        if scope is not None and not (outer and scope.explicit_requests)
        else None
    )
    if scope is not None and attempt is not None:
        if scope.explicit_requests:
            attempt.observation_scope = "provider_attempt"
        scope.attempts.append(attempt)
    try:
        yield
    except BaseException as error:
        if attempt is not None:
            attempt.fail(error)
        raise
    finally:
        if attempt is not None:
            attempt.duration_seconds = max(monotonic() - attempt.started, 0.0)


def observe_embedding(provider, *, owns_requests=False):
    """Keep post-request usage extraction in the same observation as the call."""

    def decorate(function):
        @contextmanager
        def entry(backend):
            if current_scope(backend) is not None:
                yield
                return
            scope = EmbeddingScope(
                backend, getattr(backend, "_provider", provider), explicit_requests=owns_requests
            )
            token = _scopes.set((*_scopes.get(), scope))
            try:
                yield
            except BaseException as error:
                if scope.attempts:
                    scope.attempts[-1].fail(error)
                raise
            finally:
                _scopes.reset(token)
                for attempt in scope.attempts:
                    try:
                        attempt.settle()
                    except Exception as error:
                        logging.getLogger(__name__).info(
                            "embedding observation unavailable error_type=%s", type(error).__name__
                        )

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def asynchronous(backend, *args, **kwargs):
                with entry(backend):
                    return await function(backend, *args, **kwargs)

            return asynchronous

        @functools.wraps(function)
        def synchronous(backend, *args, **kwargs):
            with entry(backend):
                return function(backend, *args, **kwargs)

        return synchronous

    return decorate
