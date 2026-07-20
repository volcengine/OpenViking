# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped provider header templates for upstream model calls."""

from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional


_HEADER_TEMPLATE_RE = re.compile(r"\$\{header:([^}]+)\}")


def referenced_template_headers(extra_headers: Mapping[str, Any] | None) -> set[str]:
    referenced: set[str] = set()
    for value in (extra_headers or {}).values():
        if not isinstance(value, str):
            continue
        for match in _HEADER_TEMPLATE_RE.finditer(value):
            header_name = match.group(1).strip()
            if header_name:
                referenced.add(header_name)
    return referenced


@dataclass(frozen=True)
class ProviderRequestContext:
    headers: dict[str, str]

    @classmethod
    def from_headers(
        cls,
        headers: Mapping[str, str],
        *,
        allowed_headers: set[str],
    ) -> Optional["ProviderRequestContext"]:
        allowed_lookup = {name.lower(): name for name in allowed_headers if name.strip()}
        if not allowed_lookup:
            return None

        selected: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            configured_name = allowed_lookup.get(key.lower())
            if configured_name is None:
                continue
            normalized_value = value.strip()
            if normalized_value:
                selected[configured_name] = normalized_value
        return cls(selected) if selected else None

    @classmethod
    def from_dict(cls, value: Any) -> Optional["ProviderRequestContext"]:
        if not isinstance(value, dict):
            return None
        raw_headers = value.get("headers", value)
        if not isinstance(raw_headers, dict):
            return None
        headers = {
            str(key): str(header_value).strip()
            for key, header_value in raw_headers.items()
            if str(key).strip() and str(header_value).strip()
        }
        return cls(headers) if headers else None

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {"headers": dict(self.headers)}

    def get_header(self, name: str) -> Optional[str]:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


_CURRENT_PROVIDER_CONTEXT: contextvars.ContextVar[ProviderRequestContext | None] = (
    contextvars.ContextVar("openviking_provider_request_context", default=None)
)


def get_provider_request_context() -> ProviderRequestContext | None:
    return _CURRENT_PROVIDER_CONTEXT.get()


def set_provider_request_context(ctx: ProviderRequestContext | None) -> contextvars.Token:
    return _CURRENT_PROVIDER_CONTEXT.set(ctx)


def reset_provider_request_context(token: contextvars.Token) -> None:
    _CURRENT_PROVIDER_CONTEXT.reset(token)


@contextmanager
def bind_provider_request_context(ctx: ProviderRequestContext | None) -> Iterator[None]:
    token = _CURRENT_PROVIDER_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _CURRENT_PROVIDER_CONTEXT.reset(token)


def resolve_dynamic_extra_headers(extra_headers: Mapping[str, Any] | None) -> dict[str, str]:
    ctx = get_provider_request_context()
    resolved: dict[str, str] = {}

    for key, value in (extra_headers or {}).items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, str):
            resolved[key] = str(value)
            continue

        missing_dynamic_value = False

        def replace(match: re.Match[str]) -> str:
            nonlocal missing_dynamic_value
            header_value = ctx.get_header(match.group(1).strip()) if ctx else None
            if header_value is None:
                missing_dynamic_value = True
                return ""
            return header_value

        rendered = _HEADER_TEMPLATE_RE.sub(replace, value)
        if missing_dynamic_value:
            continue
        if rendered.strip():
            resolved[key] = rendered

    return resolved

