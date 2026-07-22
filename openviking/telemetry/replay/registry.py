# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ReplayError


@dataclass(frozen=True, slots=True)
class EntryRegistration:
    name: str
    module: str
    owner_qualname: str | None
    function: Callable[..., Any]


_ENTRIES: dict[str, EntryRegistration] = {}
_COMPONENT_PROVIDERS: dict[type[Any], Callable[[], Any]] = {}


def register_entry(
    name: str,
    module: str,
    function: Callable[..., Any],
    owner_qualname: str | None = None,
) -> None:
    _ENTRIES[name] = EntryRegistration(name, module, owner_qualname, function)


def resolve_entry(name: str) -> EntryRegistration:
    try:
        return _ENTRIES[name]
    except KeyError as error:
        raise ReplayError(f"Replay entry {name!r} is not registered") from error


def register_component(component_type: type[Any], provider: Callable[[], Any]) -> None:
    _COMPONENT_PROVIDERS[component_type] = provider


def resolve_component(component_type: type[Any]) -> Any:
    try:
        provider = _COMPONENT_PROVIDERS[component_type]
    except KeyError as error:
        raise ReplayError(f"No replay component provider for {component_type!r}") from error
    return provider()
