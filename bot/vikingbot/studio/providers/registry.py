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
