# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from typing import Any, TypeVar, cast

from openviking.telemetry.tracer import tracer

from .codecs import ReplayCodec, encode_value, register_codec
from .json_attributes import encode_json_attribute
from .registry import register_component, register_entry
from .runtime import current_replay_session

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")


def codec(value_type: type[T], *, name: str):
    def decorate(codec_type: type[ReplayCodec[T]]) -> type[ReplayCodec[T]]:
        return register_codec(value_type, name, codec_type)

    return decorate


def component(component_type: type[T]):
    def decorate(provider: Callable[[], T]) -> Callable[[], T]:
        register_component(component_type, provider)
        return provider

    return decorate


def entry(name: str) -> Callable[[F], F]:
    def decorate(function: F) -> F:
        wrapped = _decorate_call(function, name=name, kind="entry", match_names=None)
        owner_qualname = function.__qualname__.rsplit(".", 1)[0]
        if owner_qualname == function.__qualname__ or "<locals>" in owner_qualname:
            owner_qualname = None
        register_entry(name, function.__module__, wrapped, owner_qualname)
        wrapped.__replay_entry_name__ = name
        return cast(F, wrapped)

    return decorate


def mock(name: str, *, match: Sequence[str]) -> Callable[[F], F]:
    if not match:
        raise ValueError("Replay mocks require at least one match argument")

    def decorate(function: F) -> F:
        wrapped = _decorate_call(function, name=name, kind="mock", match_names=tuple(match))
        wrapped.__replay_mock_name__ = name
        return cast(F, wrapped)

    return decorate


def _decorate_call(
    function: F,
    *,
    name: str,
    kind: str,
    match_names: tuple[str, ...] | None,
) -> F:
    signature = inspect.signature(function)
    is_async = inspect.iscoroutinefunction(function)

    def encoded_arguments(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = {
            key: value for key, value in bound.arguments.items() if key not in {"self", "cls"}
        }
        if match_names is not None:
            values = {key: values[key] for key in match_names}
        return encode_value(values)

    def annotate(span: Any, key: str, value: Any) -> None:
        span.set_attribute(key, value if isinstance(value, str) else encode_json_attribute(value))

    def start(arguments: dict[str, Any]):
        context_manager = tracer.start_as_current_span(f"replay.{kind}:{name}")
        span = context_manager.__enter__()
        annotate(span, "replay.kind", kind)
        annotate(span, "replay.name", name)
        annotate(span, "replay.module", function.__module__)
        annotate(span, "replay.arguments" if kind == "entry" else "replay.match", arguments)
        return context_manager, span

    def finish_returned(span: Any, result: Any) -> Any:
        annotate(span, "replay.outcome", "returned")
        annotate(span, "replay.result", encode_value(result))
        return result

    def finish_raised(span: Any, error: BaseException) -> None:
        annotate(span, "replay.outcome", "raised")
        annotate(
            span,
            "replay.exception",
            encode_value({"type": type(error).__qualname__, "message": str(error)}),
        )
        span.record_exception(error)

    if is_async:

        @functools.wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            arguments = encoded_arguments(args, kwargs)
            session = current_replay_session()
            if kind == "mock" and session is not None:
                return session.consume(name, arguments)
            context_manager, span = start(arguments)
            try:
                return finish_returned(span, await function(*args, **kwargs))
            except BaseException as error:
                finish_raised(span, error)
                raise
            finally:
                context_manager.__exit__(None, None, None)

        return cast(F, async_wrapper)

    @functools.wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        arguments = encoded_arguments(args, kwargs)
        session = current_replay_session()
        if kind == "mock" and session is not None:
            return session.consume(name, arguments)
        context_manager, span = start(arguments)
        try:
            return finish_returned(span, function(*args, **kwargs))
        except BaseException as error:
            finish_raised(span, error)
            raise
        finally:
            context_manager.__exit__(None, None, None)

    return cast(F, sync_wrapper)
