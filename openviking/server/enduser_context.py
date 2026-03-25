# liclaw: 整个文件为 liclaw 新增，用于 X-EndUser-Tag 透传
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""End-user tag 上下文透传模块。

将来自客户端（如 OpenClaw）的 X-EndUser-Tag 请求头透传到下游 LLM/Embedding API 调用中，
用于使用量归因和审计追踪。基于 contextvars.ContextVar 实现，异步安全，零函数签名改动。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

_ENDUSER_TAG: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openviking_enduser_tag",
    default=None,
)


def get_enduser_tag() -> Optional[str]:
    """获取当前异步上下文中的 end-user tag，无则返回 None。"""
    return _ENDUSER_TAG.get()


def get_enduser_extra_headers() -> Dict[str, str]:
    """构建包含 X-EndUser-Tag 的 extra_headers 字典，无值时返回空字典。"""
    tag = _ENDUSER_TAG.get()
    if tag:
        return {"X-EndUser-Tag": tag}
    return {}


@contextmanager
def bind_enduser_tag(tag: Optional[str]) -> Iterator[None]:
    """将 end-user tag 绑定到当前异步上下文，请求结束后自动恢复。"""
    if tag is None:
        yield
        return
    token = _ENDUSER_TAG.set(tag)
    try:
        yield
    finally:
        _ENDUSER_TAG.reset(token)
