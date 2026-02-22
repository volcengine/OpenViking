# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Provider Registry — single source of truth for LLM provider metadata.

Adding a new provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Use it in config with providers["newprovider"] = {"api_key": "xxx"}
  Done. Env vars, prefixing, config matching, status display all derive from here.

Order matters — it controls match priority and fallback. Gateways first.
Every entry writes out all fields so you can copy-paste as a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ProviderSpec:
    """VLM Provider metadata definition.

    Placeholders in env_extras values:
      {api_key}  - the user's API key
      {api_base} - api_base from config, or this spec's default_api_base
    """

    name: str
    keywords: tuple[str, ...]
    env_key: str
    display_name: str = ""

    litellm_prefix: str = ""
    skip_prefixes: tuple[str, ...] = ()

    env_extras: tuple[tuple[str, str], ...] = ()

    is_gateway: bool = False
    is_local: bool = False
    detect_by_key_prefix: str = ""
    detect_by_base_keyword: str = ""
    default_api_base: str = ""

    strip_model_prefix: bool = False

    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="OPENAI_API_KEY",
        display_name="Custom",
        litellm_prefix="openai",
        skip_prefixes=("openai/",),
        is_gateway=True,
        strip_model_prefix=True,
    ),

    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        litellm_prefix="openrouter",
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
    ),

    ProviderSpec(
        name="volcengine",
        keywords=("doubao", "volcengine", "ep-"),
        env_key="VOLCENGINE_API_KEY",
        display_name="VolcEngine",
        litellm_prefix="volcengine",
        skip_prefixes=("volcengine/",),
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
    ),

    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        litellm_prefix="",
    ),

    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        litellm_prefix="",
    ),

    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        litellm_prefix="deepseek",
        skip_prefixes=("deepseek/",),
    ),

    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        litellm_prefix="gemini",
        skip_prefixes=("gemini/",),
    ),

    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        litellm_prefix="moonshot",
        skip_prefixes=("moonshot/",),
        env_extras=(
            ("MOONSHOT_API_BASE", "{api_base}"),
        ),
        default_api_base="https://api.moonshot.ai/v1",
        model_overrides=(
            ("kimi-k2.5", {"temperature": 1.0}),
        ),
    ),

    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        litellm_prefix="zai",
        skip_prefixes=("zhipu/", "zai/"),
        env_extras=(
            ("ZHIPUAI_API_KEY", "{api_key}"),
        ),
    ),

    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        litellm_prefix="dashscope",
        skip_prefixes=("dashscope/",),
    ),

    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        litellm_prefix="minimax",
        skip_prefixes=("minimax/",),
        default_api_base="https://api.minimax.io/v1",
    ),

    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        litellm_prefix="hosted_vllm",
        is_local=True,
    ),
)


def find_by_model(model: str) -> ProviderSpec | None:
    """Match a standard provider by model-name keyword (case-insensitive).
    Skips gateways/local — those are matched by api_key/api_base instead."""
    model_lower = model.lower()
    for spec in PROVIDERS:
        if spec.is_gateway or spec.is_local:
            continue
        if any(kw in model_lower for kw in spec.keywords):
            return spec
    return None


def find_gateway(
    provider_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ProviderSpec | None:
    """Detect gateway/local provider.

    Priority:
      1. provider_name — if it maps to a gateway/local spec, use it directly.
      2. api_key prefix — e.g. "sk-or-" → OpenRouter.
      3. api_base keyword — e.g. "aihubmix" in URL → AiHubMix.
    """
    if provider_name:
        spec = find_by_name(provider_name)
        if spec and (spec.is_gateway or spec.is_local):
            return spec

    for spec in PROVIDERS:
        if spec.detect_by_key_prefix and api_key and api_key.startswith(spec.detect_by_key_prefix):
            return spec

    for spec in PROVIDERS:
        if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
            return spec

    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "dashscope"."""
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None


def get_all_provider_names() -> list[str]:
    """Get all provider names list."""
    return [spec.name for spec in PROVIDERS]
