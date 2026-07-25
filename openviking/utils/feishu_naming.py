# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Feishu document title → VikingFS resource naming helpers."""

from __future__ import annotations

from urllib.parse import urlparse

_FEISHU_HOSTS = ("feishu.cn", "larksuite.com", "larkoffice.com")


def is_feishu_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not any(host == h or host.endswith(f".{h}") for h in _FEISHU_HOSTS):
        return False
    path = parsed.path or ""
    return any(path == f"/{t}" or path.startswith(f"/{t}/") for t in ("docx", "wiki", "sheets", "base"))


def feishu_title_to_resource_segment(title: str) -> str:
    """Map a Feishu document title to one URI/storage segment.

    The title is treated as display text, not as a filesystem path: slashes are
    normalized to underscores *before* sanitization so a title like ``a/b`` is
    kept as ``a_b`` instead of being truncated to its last path component. The
    resulting segment follows VikingURI's regular safe-path style (including its
    length cap), and the resource directory and its primary ``.md`` file share
    this one segment.
    """
    from openviking_cli.utils.uri import VikingURI

    text = (title or "").strip()
    if not text:
        return "unnamed"

    text = text.replace("\\", "_").replace("/", "_")
    return VikingURI.sanitize_segment(text)

