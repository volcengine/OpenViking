# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from typing import Any

from .codecs import decode_value
from .models import EntryRecord, MockRecord, ReplayCodecError, ReplayResult
from .registry import resolve_component, resolve_entry
from .runtime import ReplaySession, bind_replay_session


class ReplayRunner:
    def __init__(
        self,
        *,
        component_providers: dict[type[Any], Callable[[], Any]] | None = None,
    ) -> None:
        self._component_providers = dict(component_providers or {})

    async def run(
        self,
        entry_record: EntryRecord,
        mock_records: list[MockRecord],
    ) -> ReplayResult:
        module = importlib.import_module(entry_record.module)
        registration = resolve_entry(entry_record.name)
        arguments = decode_value(entry_record.arguments)
        if not isinstance(arguments, dict):
            raise ReplayCodecError("Replay entry arguments must decode to a dictionary")
        function = registration.function
        if registration.owner_qualname is not None:
            owner_type = _resolve_owner_type(module, registration.owner_qualname)
            provider = self._component_providers.get(owner_type)
            component = provider() if provider is not None else resolve_component(owner_type)
            function = function.__get__(component, owner_type)

        session = ReplaySession.from_records(mock_records)
        try:
            with bind_replay_session(session):
                result = function(**arguments)
                if inspect.isawaitable(result):
                    result = await result
            return ReplayResult(
                outcome="returned",
                result=result,
                unconsumed_records=session.unconsumed_records,
            )
        except BaseException as error:
            return ReplayResult(
                outcome="raised",
                exception=error,
                unconsumed_records=session.unconsumed_records,
            )


def _resolve_owner_type(module: Any, qualname: str) -> type[Any]:
    owner: Any = module
    for part in qualname.split("."):
        owner = getattr(owner, part)
    if not isinstance(owner, type):
        raise ReplayCodecError(f"Replay entry owner {qualname!r} is not a type")
    return owner
