"""One content-free observation around an existing provider invocation.

This adds no retry or admission owner. An SDK invocation can fail before any
bytes are sent; it is not a transport/billing receipt. Clients used here disable
SDK retries so retries remain visible as separate invocations at the VLM owner.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from contextlib import contextmanager
from uuid import uuid4

from openviking.metrics.datasources.model_usage import reported_token_count, response_usage_subsets
from openviking.models.model_error import ModelErrorKind, model_error_kind
from openviking.utils.model_retry import extract_metric_error_code


def field(value, name):
    try:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    except Exception:
        return None


def public_response_usage(usage):
    """Keep unknown numeric fields absent in the legacy response dictionary."""
    values = {
        key: value
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if (value := reported_token_count(field(usage, key))) is not None
    }
    if "total_tokens" not in values and {"prompt_tokens", "completion_tokens"}.issubset(values):
        values["total_tokens"] = values["prompt_tokens"] + values["completion_tokens"]
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        value = field(usage, key)
        if usage is not None:
            values[key] = value
    return values


class ProviderAttempt:
    def __init__(self, backend, api, model_name=None):
        self.backend, self.api = backend, api
        self.model_name = model_name or backend.model or "unknown"
        self.attempt_id = "attempt_" + uuid4().hex
        self.started = time.perf_counter()
        self.duration_seconds = 0.0
        self.usage: dict[str, int] = {}
        self.outcome, self.error_code = "success", "OK"
        self._settled = False

    def observe_usage(self, usage):
        if usage is None:
            return
        input_name, output_name = (
            ("input_tokens", "output_tokens")
            if self.api == "responses"
            else ("prompt_tokens", "completion_tokens")
        )
        self.usage = {}
        for target, name in [("prompt_tokens", input_name), ("completion_tokens", output_name)]:
            value = reported_token_count(field(usage, name))
            if value is not None:
                self.usage[target] = value
        try:
            reported = response_usage_subsets(
                usage, self.usage.get("prompt_tokens"), self.usage.get("completion_tokens")
            )
            subsets: dict[str, int | None] = {
                "prompt_cached_tokens": reported["prompt_cached_tokens"],
                "completion_reasoning_tokens": reported["completion_reasoning_tokens"],
            }
        except Exception:
            subsets = {}
        for subset_name, subset_value in subsets.items():
            if subset_value is not None:
                self.usage[subset_name] = subset_value

    def observe_response(self, response):
        self.observe_usage(field(response, "usage"))
        choices = field(response, "choices") or []
        for choice in choices if isinstance(choices, (list, tuple)) else []:
            if field(choice, "finish_reason") == "content_filter" or field(
                field(choice, "message"), "refusal"
            ):
                self.outcome, self.error_code = "refused", "content_safety"
        if self.api == "responses" and field(response, "status") not in {None, "completed"}:
            self.outcome, self.error_code = "failed", "provider_status"

    def fail(self, error):
        if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
            self.outcome, self.error_code = "cancelled", "cancelled"
        else:
            self.outcome = (
                "refused" if model_error_kind(error) == ModelErrorKind.CONTENT_SAFETY else "failed"
            )
            self.error_code = extract_metric_error_code(error)

    def finish(self):
        if self._settled:
            return
        self._settled = True
        self.duration_seconds = time.perf_counter() - self.started
        self.backend.update_token_usage(
            model_name=self.model_name,
            provider=self.backend.provider,
            prompt_tokens=self.usage.get("prompt_tokens"),
            completion_tokens=self.usage.get("completion_tokens"),
            prompt_cached_tokens=self.usage.get("prompt_cached_tokens"),
            completion_reasoning_tokens=self.usage.get("completion_reasoning_tokens"),
            duration_seconds=self.duration_seconds,
            attempt_id=self.attempt_id,
            api=self.api,
            outcome=self.outcome,
            error_code=self.error_code,
            usage_complete=self.outcome in {"success", "refused"},
            observation_scope="adapter_call"
            if self.backend.provider == "openai-codex"
            else "provider_attempt",
        )


@contextmanager
def observe_attempt(backend, *, api="chat.completions", model_name=None):
    attempt = ProviderAttempt(backend, api, model_name)
    try:
        yield attempt
    except BaseException as error:
        try:
            attempt.fail(error)
        except Exception:
            attempt.outcome, attempt.error_code = "failed", "unknown"
        raise
    finally:
        try:
            attempt.finish()
        except Exception:
            # Usage observation must never turn inference into a failure or
            # replace the original cancellation/provider exception.
            from openviking_cli.utils import get_logger

            get_logger(__name__).info("VLM attempt observation unavailable degraded=true")
