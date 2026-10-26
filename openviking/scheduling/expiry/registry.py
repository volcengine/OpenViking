# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Private resource handler registry."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .api import SUPPORTED_ITEM_TYPES, ExpiryError, ExpiryHandler, HandlerOptions
from .models import validate_item_type


@dataclass(frozen=True)
class HandlerBinding:
    handler: ExpiryHandler
    options: HandlerOptions


class HandlerRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, HandlerBinding] = {}
        self._frozen = False

    def register(self, item_type: str, handler: ExpiryHandler, options: HandlerOptions) -> None:
        if self._frozen:
            raise ExpiryError("registration_closed", "Timer handler registration is closed")
        validate_item_type(item_type)
        if item_type not in SUPPORTED_ITEM_TYPES:
            raise ExpiryError("unregistered_type", f"Timer type is not supported: {item_type}")
        handle = getattr(handler, "handle", None)
        if not callable(handle) or not inspect.iscoroutinefunction(handle):
            raise ExpiryError("invalid_handler", "Timer handler.handle must be async")
        if not isinstance(options, HandlerOptions):
            raise ExpiryError("invalid_handler", "Invalid timer handler options")
        if type(options.concurrency) is not int or options.concurrency <= 0:
            raise ExpiryError("invalid_handler", "Handler concurrency must be positive")
        if type(options.max_attempts) is not int or not 1 <= options.max_attempts <= 256:
            raise ExpiryError("invalid_handler", "Handler max_attempts must be in [1, 256]")
        if type(options.metadata_enabled) is not bool:
            raise ExpiryError("invalid_handler", "Handler metadata_enabled must be a boolean")
        if item_type in self._bindings:
            raise ExpiryError("duplicate_handler", f"Handler already registered: {item_type}")
        self._bindings[item_type] = HandlerBinding(handler, options)

    def get(self, item_type: str) -> HandlerBinding | None:
        return self._bindings.get(item_type)

    def freeze(self) -> Mapping[str, HandlerBinding]:
        self._frozen = True
        return MappingProxyType(dict(self._bindings))
