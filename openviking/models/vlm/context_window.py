# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Prompt budget derived from the context window a VLM backend really uses.

Phase-2 memory extraction asks the model to emit a single Python-DSL program that
describes every memory operation for a batch of messages. Such a request is
prompt-heavy *and* output-heavy: once the prompt occupies most of the model
context window, the program is cut off mid-literal and ``extract_loop`` reports
``failure_kind=parse_error`` instead of a truncation error. Extraction batching
therefore has to be sized from the window the backend actually requests, not from
a fixed token constant tuned for large-context hosted models.

Window resolution order:

1. Ollama routes: ``extra_request_body.num_ctx`` when set, otherwise the same
   default ``litellm_vlm`` sends (``OLLAMA_DEFAULT_NUM_CTX``). This is exactly the
   value that bounds the prompt server-side.
2. Other routes: LiteLLM's model registry (``max_input_tokens``).
3. Unknown models: no limit is returned, so callers keep their previous behavior.
"""

from __future__ import annotations

from typing import Any, Optional

# Room an extraction pass needs for the DSL program it writes back.
DEFAULT_OUTPUT_RESERVE = 4_096
# Room for the extraction protocol (tool schemas, DSL fragments) and the memory
# pages that are attached to every pass; it is not part of the message material.
DEFAULT_PROTOCOL_RESERVE = 2_048
# Below this there is no point splitting: a single message would not fit either.
MIN_PROMPT_TOKEN_LIMIT = 1_024
# Keep ~5% of the window free for chat templates and token-estimator drift.
SAFETY_RESERVE_DIVISOR = 20

_OLLAMA_PROVIDERS = frozenset({"ollama", "ollama_chat"})
_OLLAMA_MODEL_PREFIXES = ("ollama/", "ollama_chat/")


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _setting(vlm: Any, name: str) -> Any:
    if vlm is None:
        return None
    return getattr(vlm, name, None)


def _is_ollama_route(vlm: Any) -> bool:
    model = str(_setting(vlm, "model") or "").strip().lower()
    provider = str(_setting(vlm, "provider") or "").strip().lower()
    return provider in _OLLAMA_PROVIDERS or model.startswith(_OLLAMA_MODEL_PREFIXES)


def _ollama_num_ctx(vlm: Any) -> Optional[int]:
    from openviking.models.vlm.backends.litellm_vlm import OLLAMA_DEFAULT_NUM_CTX

    extra_request_body = _setting(vlm, "extra_request_body")
    configured = (
        _positive_int(extra_request_body.get("num_ctx"))
        if isinstance(extra_request_body, dict)
        else None
    )
    return configured or OLLAMA_DEFAULT_NUM_CTX


def _registered_input_tokens(model: Any) -> Optional[int]:
    if not isinstance(model, str) or not model:
        return None
    try:
        from litellm import model_cost

        entry = model_cost.get(model)
        if entry is None and "/" in model:
            entry = model_cost.get(model.split("/", 1)[1])
    except Exception:  # noqa: BLE001 - registry lookup is best effort
        return None
    if isinstance(entry, dict):
        return _positive_int(entry.get("max_input_tokens"))
    return None


def resolve_context_window(vlm: Any) -> Optional[int]:
    """Context window the backend will actually use, or ``None`` if unknown."""
    if _is_ollama_route(vlm):
        return _ollama_num_ctx(vlm)
    return _registered_input_tokens(_setting(vlm, "model"))


def resolve_prompt_token_limit(vlm: Any) -> Optional[int]:
    """Token budget the extraction *message material* may occupy on this model.

    The window is shared by three consumers, all of which are subtracted before
    the remainder is handed to batching: the program the model writes back, the
    protocol and memory pages attached to every pass, and a safety margin.

    Returns ``None`` when the window cannot be determined, which keeps callers on
    their previous (unbounded) behavior.
    """
    window = resolve_context_window(vlm)
    if window is None:
        return None
    output_reserve = _positive_int(_setting(vlm, "max_tokens")) or min(
        DEFAULT_OUTPUT_RESERVE, window // 4
    )
    output_reserve = min(output_reserve, window // 2)
    protocol_reserve = min(DEFAULT_PROTOCOL_RESERVE, window // 8)
    safety_reserve = max(256, window // SAFETY_RESERVE_DIVISOR)
    limit = window - output_reserve - protocol_reserve - safety_reserve
    return limit if limit >= MIN_PROMPT_TOKEN_LIMIT else None


__all__ = [
    "DEFAULT_OUTPUT_RESERVE",
    "MIN_PROMPT_TOKEN_LIMIT",
    "resolve_context_window",
    "resolve_prompt_token_limit",
]
