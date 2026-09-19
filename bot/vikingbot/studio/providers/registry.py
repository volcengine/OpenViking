"""Explicit platform registration. Unknown types never fall back to Feishu."""

from fastapi import HTTPException

from vikingbot.studio.providers.feishu.provider import FeishuProvider

PROVIDERS = {"feishu": FeishuProvider()}


def get_provider(record):
    # Records written before platform support were all Feishu connections.
    platform = record.get("type", "feishu")
    if platform not in PROVIDERS:
        raise HTTPException(400, "Unsupported IM type")
    return PROVIDERS[platform]


def validate_settings(provider, value):
    if hasattr(provider, "validate_settings"):
        return provider.validate_settings(value)
    if value:
        raise HTTPException(400, "Settings are unavailable for this platform")
    return {}
