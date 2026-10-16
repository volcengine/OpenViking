# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Translate account-effective VLM credentials into gateway upstream candidates."""

import re
from urllib.parse import urlsplit

from openviking_cli.utils.config.vlm_config import VLMConfig

# LiteLLM routes that speak OpenAI chat at api_base; LiteLLM sends the model name without them.
OPENAI_CHAT_ROUTES = (
    "openai/",
    "hosted_vllm/",
    "openrouter/",
    "deepseek/",
    "dashscope/",
    "moonshot/",
    "nvidia_nim/",
    "zai/",
    "groq/",
    "together_ai/",
    "fireworks_ai/",
    "deepinfra/",
    "xai/",
    "lm_studio/",
)
# LiteLLM routes with their own wire protocol or cloud credentials; the gateway cannot forward them.
UNSUPPORTED_ROUTES = (
    "gemini/",
    "vertex_ai/",
    "bedrock/",
    "sagemaker/",
    "sagemaker_chat/",
    "sagemaker_nova/",
    "cohere/",
    "cohere_chat/",
    "github_copilot/",
    "minimax/",
)


def _vendor(base: str) -> str:
    host = (urlsplit(base).hostname or "").lower()
    for suffix, vendor in (
        ("volces.com", "ark"),
        ("bytepluses.com", "byteplus"),
        ("byteplus.com", "byteplus"),
        ("openai.com", "openai"),
        ("anthropic.com", "anthropic"),
        ("deepseek.com", "deepseek"),
    ):
        if host == suffix or host.endswith("." + suffix):
            return vendor
    return "generic"


def vlm_upstreams(config: VLMConfig) -> dict:
    """Produce deterministic candidates; the gateway validates and stores them."""
    upstreams, skipped = [], []
    for credential in config.credentials:
        identifier = "vlm-" + credential.id
        settings = config.build_vlm_config_dict_for_credential(credential)
        # Same default as VLMFactory.
        provider = (settings.get("provider") or "openai").lower()
        model = settings.get("model") or ""
        base = settings.get("api_base") or ""
        api_key = settings.get("api_key") or ""
        headers = dict(settings.get("extra_headers") or {})
        route = model.lower()
        protocol = "chat"
        reason = None
        if provider == "openai-codex":
            reason = "openai_codex"
        elif provider == "azure" or (provider == "litellm" and route.startswith("azure/")):
            reason = "azure"
        elif provider == "volcengine":
            from openviking.models.vlm.backends.volcengine_vlm import DEFAULT_VOLCENGINE_API_BASE

            base = base or DEFAULT_VOLCENGINE_API_BASE
            headers = {k: v for k, v in headers.items() if k.lower() != "x-client-request-id"}
        elif provider == "openai":
            base = base or "https://api.openai.com/v1"
        elif provider == "kimi":
            from openviking.models.vlm.backends.kimi_vlm import normalize_kimi_config

            settings = normalize_kimi_config(settings)
            base, model, headers = (
                settings["api_base"],
                settings["model"],
                settings["extra_headers"],
            )
        elif provider == "glm":
            from openviking.models.vlm.backends.glm_vlm import normalize_glm_config

            settings = normalize_glm_config(settings)
            base, model = settings["api_base"], settings["model"]
        elif route.startswith(("ollama/", "ollama_chat/")):
            model = model.split("/", 1)[1]
            base = base or "http://localhost:11434"
            if not urlsplit(base).path.strip("/"):
                base = base.rstrip("/") + "/v1"
            api_key = api_key or "ollama"
        elif route.startswith("anthropic/"):
            model = model.split("/", 1)[1]
            protocol = "anthropic"
            base = base or "https://api.anthropic.com"
        elif route.startswith(UNSUPPORTED_ROUTES):
            reason = "litellm_unsupported_route"
        elif not base:
            reason = "litellm_no_api_base"
        elif route.startswith(OPENAI_CHAT_ROUTES):
            model = model.split("/", 1)[1]
        if reason:
            skipped.append({"id": identifier, "reason": reason})
            continue
        upstreams.append(
            {
                "id": identifier,
                "name": credential.id,
                "protocol": protocol,
                "base_url": base,
                "api_key": api_key,
                "headers": headers,
                "models": [model] if model else [],
                "vendor": _vendor(base),
                "coding_plan": bool(re.search(r"(^|/)(coding|plan)(/|$)", urlsplit(base).path)),
            }
        )
    return {"upstreams": upstreams, "skipped": skipped}
