#!/usr/bin/env python3
"""Switchable Tau2 RolloutExecutor implementations."""

from __future__ import annotations

from typing import Any, Literal

from benchmark.tau2.train._rollout_helpers import (
    _as_tool_input,
    _safe_float,
    _stringify,
    _tau2_evaluation,
    _to_jsonable,
)
from benchmark.tau2.train.rollout_executor_native import NativeTau2RolloutExecutor
from benchmark.tau2.train.rollout_executor_vikingbot import (
    Tau2RolloutExecutor as VikingBotTau2RolloutExecutor,
)
from benchmark.tau2.train.rollout_executor_vikingbot import (  # re-export vikingbot-only helpers for tests
    _append_final_answer_for_tau2_evaluation,
    _build_rollout_messages,
    _configure_tools,
)

Tau2RolloutBackend = Literal["native", "vikingbot"]
DEFAULT_TAU2_ROLLOUT_BACKEND: Tau2RolloutBackend = "native"


def normalize_tau2_rollout_backend(value: Any) -> Tau2RolloutBackend:
    backend = str(value or DEFAULT_TAU2_ROLLOUT_BACKEND).strip().lower()
    if backend not in {"native", "vikingbot"}:
        raise ValueError("rollout_backend must be 'native' or 'vikingbot'")
    return backend  # type: ignore[return-value]


def make_tau2_rollout_executor(
    *,
    backend: Any = DEFAULT_TAU2_ROLLOUT_BACKEND,
    options: dict[str, Any] | None = None,
    config_path: str | None = None,
    concurrency: int = 1,
    rollout_language: str = "default",
):
    """Create a tau2 rollout executor for the selected backend."""

    selected = normalize_tau2_rollout_backend(backend)
    opts = dict(options or {})
    if selected == "vikingbot":
        return VikingBotTau2RolloutExecutor(
            config_path=opts.get("config_path") or config_path,
            concurrency=concurrency,
            keep_default_tools=_bool_option(opts.get("keep_default_tools"), default=True),
            max_iterations=int(opts.get("max_iterations") or 30),
            rollout_language=str(opts.get("rollout_language") or rollout_language),
        )
    return NativeTau2RolloutExecutor(
        concurrency=concurrency,
        agent_llm=_optional_str(opts.get("agent_llm")),
        user_llm=_optional_str(opts.get("user_llm")),
        agent_llm_args=_dict_option(opts.get("agent_llm_args")),
        user_llm_args=_dict_option(opts.get("user_llm_args")),
        base_agent=str(opts.get("base_agent") or "llm_agent"),
        user=str(opts.get("user") or "user_simulator"),
        max_steps=int(opts.get("max_steps") or opts.get("max_iterations") or 200),
        max_errors=int(opts.get("max_errors") or 10),
        seed=int(opts.get("seed") or 300),
        memory_enabled=_bool_option(
            opts.get("memory_enabled", opts.get("keep_default_tools")),
            default=True,
        ),
        retrieval_mode=str(opts.get("retrieval_mode") or "first_user_prewrite"),
        search_uri=str(opts.get("search_uri") or "viking://user/memories/experiences"),
        retrieval_top_k=int(opts.get("retrieval_top_k") or 4),
        first_user_retrieval_top_k=_optional_int(opts.get("first_user_retrieval_top_k")),
        first_user_inject_top_k=_optional_int(opts.get("first_user_inject_top_k")),
        prewrite_retrieval_top_k=_optional_int(opts.get("prewrite_retrieval_top_k")),
        prewrite_inject_top_k=_optional_int(opts.get("prewrite_inject_top_k")),
        memory_inject_max_chars=_optional_int(opts.get("memory_inject_max_chars")),
        first_user_memory_inject_max_chars=_optional_int(
            opts.get("first_user_memory_inject_max_chars")
        ),
        prewrite_memory_inject_max_chars=_optional_int(
            opts.get("prewrite_memory_inject_max_chars")
        ),
        openviking_url=_optional_str(opts.get("openviking_url")),
        openviking_api_key=_optional_str(opts.get("openviking_api_key")),
        openviking_account=_optional_str(opts.get("openviking_account")),
        openviking_user=_optional_str(opts.get("openviking_user")),
        openviking_timeout=float(opts.get("openviking_timeout") or 600.0),
        scope_prompt=str(opts.get("scope_prompt") or ""),
        rollout_language=str(opts.get("rollout_language") or rollout_language),
        show_progress=_bool_option(opts.get("show_progress"), default=False),
        progress_label=str(opts.get("progress_label") or "tau2"),
    )


# Historical name now points at the default backend for new construction.
Tau2RolloutExecutor = NativeTau2RolloutExecutor



def _bool_option(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean option: {value!r}")
    return bool(value)

def _dict_option(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        import json

        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("LLM args options must decode to an object")
        return parsed
    raise ValueError("LLM args options must be dict or JSON object string")


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


__all__ = [
    "DEFAULT_TAU2_ROLLOUT_BACKEND",
    "NativeTau2RolloutExecutor",
    "Tau2RolloutBackend",
    "Tau2RolloutExecutor",
    "VikingBotTau2RolloutExecutor",
    "make_tau2_rollout_executor",
    "normalize_tau2_rollout_backend",
    "_append_final_answer_for_tau2_evaluation",
    "_as_tool_input",
    "_build_rollout_messages",
    "_configure_tools",
    "_safe_float",
    "_stringify",
    "_tau2_evaluation",
    "_to_jsonable",
]
