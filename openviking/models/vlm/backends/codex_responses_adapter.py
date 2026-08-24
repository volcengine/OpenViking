# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Generic,
    List,
    Mapping,
    Optional,
    Tuple,
    TypeVar,
)
from urllib.parse import urlsplit

_T = TypeVar("_T")
_APPROVED_CODEX_ORIGIN = "https://chatgpt.com/backend-api/codex"


class CodexStateValidationError(ValueError):
    """The caller-managed state or request violates the state contract."""


class CodexStateExpiredError(CodexStateValidationError):
    """The caller-managed state exceeded its hard replay TTL."""


class CodexStateGenerationError(CodexStateValidationError):
    """The caller did not bind the exact state generation."""


class CodexStateBindingError(CodexStateValidationError):
    """A model, instruction, origin, principal, or credential binding changed."""


class CodexToolCallIntegrityError(CodexStateValidationError):
    """A tool-call capability was unknown, duplicated, stale, or forked."""


class CodexStateLimitError(CodexStateValidationError):
    """A reviewed state-mode resource limit was exceeded."""


class CodexCapabilityError(CodexStateValidationError):
    """The requested endpoint feature has not been explicitly verified."""


class CodexStateConcurrencyError(CodexStateValidationError):
    """The per-adapter in-flight state request limit was reached."""


class CodexStateTransportError(RuntimeError):
    """A provider transport failed without exposing provider-controlled details."""


@dataclass(frozen=True)
class CodexResponsesLimits:
    max_bytes: int = 32 * 1024 * 1024
    max_items: int = 4096
    max_turns: int = 256
    max_images: int = 8
    max_image_bytes: int = 8 * 1024 * 1024
    max_tool_output_bytes: int = 1024 * 1024
    max_total_tool_output_bytes: int = 4 * 1024 * 1024
    max_tool_call_ids: int = 4096
    max_tool_call_id_bytes: int = 512
    ttl_seconds: int = 3600
    max_concurrent_chains: int = 16

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.__dict__.values()):
            raise ValueError("Codex Responses limits must be positive.")


@dataclass(frozen=True)
class CodexResponsesState:
    chain_id: str = field(repr=False)
    generation: int
    model: str
    instructions_digest: str
    origin: str
    principal_fingerprint: str = field(repr=False)
    credential_fingerprint: str = field(repr=False)
    expires_at: datetime
    response_items: Tuple[Mapping[str, Any], ...] = field(repr=False)
    open_tool_call_ids: FrozenSet[str] = field(repr=False)
    turn_count: int
    image_count: int = 0
    total_tool_output_bytes: int = 0
    seen_tool_call_ids: FrozenSet[str] = field(default_factory=frozenset, repr=False)
    integrity_tag: str = field(default="", repr=False)


@dataclass(frozen=True)
class CodexResponsesTurn(Generic[_T]):
    result: _T = field(repr=False)
    state: CodexResponsesState = field(repr=False)


def _to_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _to_json_value(model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return _to_json_value(vars(value))
    raise CodexStateValidationError("Responses state contains a non-JSON value.")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_deep_thaw(item) for item in value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _deep_thaw(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_origin(origin: str) -> str:
    try:
        parsed = urlsplit(str(origin).strip())
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise CodexStateBindingError("Codex state origin is invalid.") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise CodexStateBindingError("Codex state origin is invalid.")
    path = parsed.path.rstrip("/")
    return f"https://{parsed.hostname.lower()}{path}"


def _instructions_digest(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def _state_payload(state: CodexResponsesState) -> Dict[str, Any]:
    return {
        "chain_id": state.chain_id,
        "generation": state.generation,
        "model": state.model,
        "instructions_digest": state.instructions_digest,
        "origin": state.origin,
        "principal_fingerprint": state.principal_fingerprint,
        "credential_fingerprint": state.credential_fingerprint,
        "expires_at": state.expires_at.astimezone(timezone.utc).isoformat(),
        "response_items": _deep_thaw(state.response_items),
        "open_tool_call_ids": sorted(state.open_tool_call_ids),
        "turn_count": state.turn_count,
        "image_count": state.image_count,
        "total_tool_output_bytes": state.total_tool_output_bytes,
        "seen_tool_call_ids": sorted(state.seen_tool_call_ids),
    }


def _state_tag(state: CodexResponsesState, key: bytes) -> str:
    return hmac.new(
        key,
        _canonical_json(_state_payload(state)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _freeze_items(items: List[Dict[str, Any]]) -> Tuple[Mapping[str, Any], ...]:
    return tuple(_deep_freeze(_to_json_value(item)) for item in items)


def _latest_compaction_window(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for index in range(len(items) - 1, -1, -1):
        if items[index].get("type") == "compaction":
            return items[index:]
    return items


def _limit_error(name: str, allowed: int, observed: int) -> CodexStateLimitError:
    return CodexStateLimitError(
        f"Codex state {name} limit exceeded: allowed={allowed}, observed={observed}."
    )


def _convert_content_for_responses(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content else ""
    converted: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            converted.append({"type": "input_text", "text": part.get("text", "")})
            continue
        if part_type == "image_url":
            image_value = part.get("image_url", {})
            url = image_value.get("url", "") if isinstance(image_value, dict) else str(image_value)
            entry: Dict[str, Any] = {"type": "input_image", "image_url": url}
            if isinstance(image_value, dict) and image_value.get("detail"):
                entry["detail"] = image_value["detail"]
            converted.append(entry)
            continue
        if part_type in {"input_text", "input_image"}:
            converted.append(part)
            continue
        text_value = part.get("text")
        if text_value:
            converted.append({"type": "input_text", "text": text_value})
    return converted or ""


def _convert_tools_for_responses(tools: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(tools, list):
        return None
    converted: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "") or "").strip()
        if not name:
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return converted or None


def _stringify_response_payload(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts: List[str] = []
        for part in value:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "input_text", "output_text"}:
                text = part.get("text")
                if text is not None:
                    text_parts.append(str(text))
        if text_parts:
            return "".join(text_parts)
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _convert_tool_call_history(tool_calls: Any) -> List[Dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    converted: List[Dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "") or "").strip()
        if not name:
            continue
        converted.append(
            {
                "type": "function_call",
                "call_id": str(tool_call.get("id", "") or tool_call.get("call_id", "") or ""),
                "name": name,
                "arguments": _stringify_response_payload(function.get("arguments"), default="{}"),
            }
        )
    return converted


def _convert_message_for_responses(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    role = str(message.get("role", "user") or "user")
    content = message.get("content")
    if role == "tool":
        return [
            {
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id", "") or message.get("call_id", "") or ""),
                "output": _stringify_response_payload(content),
            }
        ]
    if role == "assistant":
        converted: List[Dict[str, Any]] = []
        converted_content = _convert_content_for_responses(content)
        if converted_content not in ("", []):
            converted.append({"role": "assistant", "content": converted_content})
        converted.extend(_convert_tool_call_history(message.get("tool_calls")))
        return converted
    normalized_role = role if role in {"user", "assistant"} else "user"
    return [{"role": normalized_role, "content": _convert_content_for_responses(content)}]


def _item_get(obj: Any, key: str, default: Any = None) -> Any:
    value = getattr(obj, key, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(key, default)
    return default if value is None else value


async def _close_async_resources_cancellation_safe(*resources: Any) -> None:
    """Finish all async closes before propagating any repeated cancellation."""

    async def close_all() -> None:
        first_error: BaseException | None = None
        for resource in resources:
            close = getattr(resource, "aclose", None)
            if callable(close):
                try:
                    await close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error

    cleanup = asyncio.create_task(close_all())
    cancellation: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancellation = exc
    cleanup_error: BaseException | None = None
    try:
        cleanup.result()
    except BaseException as exc:
        cleanup_error = exc
    if cancellation is not None:
        raise cancellation
    if cleanup_error is not None:
        raise cleanup_error


def _build_chat_completion_like_response(final_response: Any, model: str) -> Any:
    text_parts: List[str] = []
    tool_calls: List[Any] = []
    for item in getattr(final_response, "output", []) or []:
        item_type = _item_get(item, "type")
        if item_type == "message":
            for part in _item_get(item, "content", []) or []:
                if _item_get(part, "type") in {"output_text", "text"}:
                    text_parts.append(str(_item_get(part, "text", "")))
            continue
        if item_type == "function_call":
            tool_calls.append(
                SimpleNamespace(
                    id=_item_get(item, "call_id", ""),
                    type="function",
                    function=SimpleNamespace(
                        name=_item_get(item, "name", ""),
                        arguments=_item_get(item, "arguments", "{}"),
                    ),
                )
            )
    usage_raw = getattr(final_response, "usage", None)
    usage = None
    if usage_raw is not None:
        usage = SimpleNamespace(
            prompt_tokens=getattr(usage_raw, "input_tokens", 0),
            completion_tokens=getattr(usage_raw, "output_tokens", 0),
            total_tokens=getattr(usage_raw, "total_tokens", 0),
        )
    message = SimpleNamespace(
        role="assistant",
        content="".join(text_parts).strip() or None,
        tool_calls=tool_calls or None,
    )
    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason="tool_calls" if tool_calls else "stop",
    )
    return SimpleNamespace(
        choices=[choice],
        model=model,
        usage=usage,
    )


def _build_final_response_from_stream_events(
    completed_response: Any,
    collected_output_items: List[Any],
    collected_text_deltas: List[str],
    has_function_calls: bool,
) -> Any:
    output = getattr(completed_response, "output", None) if completed_response is not None else None
    if output:
        return completed_response

    fallback_output: List[Any] = []
    if collected_output_items:
        fallback_output = list(collected_output_items)
    elif collected_text_deltas and not has_function_calls:
        fallback_output = [
            SimpleNamespace(
                type="message",
                role="assistant",
                status="completed",
                content=[SimpleNamespace(type="output_text", text="".join(collected_text_deltas))],
            )
        ]

    return SimpleNamespace(
        output=fallback_output,
        usage=getattr(completed_response, "usage", None)
        if completed_response is not None
        else None,
    )


class CodexCompletionsAdapter:
    def __init__(
        self,
        client_factory: Callable[[], Any],
        model: str,
        *,
        async_client_factory: Optional[Callable[[], Any]] = None,
        state_integrity_key: Optional[bytes] = None,
        origin: str = _APPROVED_CODEX_ORIGIN,
        principal_fingerprint: str = "principal:v1:codex",
        credential_fingerprint: str = "credential:v1:codex",
        state_limits: Optional[CodexResponsesLimits] = None,
        clock: Optional[Callable[[], datetime]] = None,
        responses_compact_threshold: Optional[int] = None,
        responses_sdk_version: str = "unknown",
    ):
        self._client_factory = client_factory
        self._async_client_factory = async_client_factory
        self._model = model
        self._state_integrity_key = state_integrity_key or secrets.token_bytes(32)
        if not isinstance(self._state_integrity_key, bytes) or len(self._state_integrity_key) < 16:
            raise ValueError("Codex state integrity key must contain at least 16 bytes.")
        self._origin = _canonical_origin(origin)
        self._principal_fingerprint = str(principal_fingerprint)
        self._credential_fingerprint = str(credential_fingerprint)
        self._state_limits = state_limits or CodexResponsesLimits()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._responses_compact_threshold = responses_compact_threshold
        self._responses_sdk_version = str(responses_sdk_version)
        self._compaction_capability_stamp: Optional[str] = None
        self._request_lock = threading.Lock()
        self._in_flight = 0
        self._initial_in_flight = 0
        self._chain_registry: Dict[str, Dict[str, Any]] = {}

    def _compaction_capability_payload(self) -> Dict[str, Any]:
        return {
            "schema": "responses-context-management-v1",
            "item_type": "compaction",
            "model": self._model,
            "origin": self._origin,
            "principal": self._principal_fingerprint,
            "credential": self._credential_fingerprint,
            "sdk_version": self._responses_sdk_version,
            "threshold": self._responses_compact_threshold,
        }

    def _sign_compaction_capability(self) -> str:
        return hmac.new(
            self._state_integrity_key,
            _canonical_json(self._compaction_capability_payload()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _has_compaction_capability(self) -> bool:
        stamp = self._compaction_capability_stamp
        return bool(stamp and hmac.compare_digest(stamp, self._sign_compaction_capability()))

    def _create_response(self, **kwargs) -> Any:
        client = self._client_factory()
        messages = kwargs.get("messages") or []
        model = kwargs.get("model") or self._model
        instructions_parts: List[str] = []
        input_messages: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user") or "user")
            content = message.get("content") or ""
            if role in {"system", "developer"}:
                instructions_parts.append(content if isinstance(content, str) else str(content))
                continue
            input_messages.extend(_convert_message_for_responses(message))
        response_kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": "\n\n".join(part for part in instructions_parts if part).strip()
            or "You are a helpful assistant.",
            "input": input_messages or [{"role": "user", "content": ""}],
            "store": False,
        }
        tools = _convert_tools_for_responses(kwargs.get("tools"))
        if tools:
            response_kwargs["tools"] = tools
        collected_output_items: List[Any] = []
        collected_text_deltas: List[str] = []
        has_function_calls = False
        completed_response = None
        stream = client.responses.create(**response_kwargs, stream=True)
        try:
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None:
                        collected_output_items.append(item)
                    continue
                if "output_text.delta" in event_type:
                    delta = getattr(event, "delta", "")
                    if delta:
                        collected_text_deltas.append(delta)
                    continue
                if "function_call" in event_type:
                    has_function_calls = True
                    continue
                if event_type == "response.completed":
                    completed_response = getattr(event, "response", None)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        final_response = _build_final_response_from_stream_events(
            completed_response,
            collected_output_items,
            collected_text_deltas,
            has_function_calls,
        )
        return _build_chat_completion_like_response(final_response, model)

    def create(self, **kwargs) -> Any:
        if kwargs.get("stream"):
            raise NotImplementedError("Streaming is not supported for openai-codex.")
        response = self._create_response(**kwargs)
        return response

    def _validate_integrity(self, state: CodexResponsesState) -> None:
        if not isinstance(state, CodexResponsesState):
            raise CodexStateValidationError("Codex state type is invalid.")
        expected = _state_tag(replace(state, integrity_tag=""), self._state_integrity_key)
        if not hmac.compare_digest(state.integrity_tag, expected):
            raise CodexStateValidationError("Codex state integrity validation failed.")

    def _validate_forbidden_kwargs(self, kwargs: Dict[str, Any]) -> None:
        forbidden = {"conversation", "previous_response_id"}
        for name in forbidden:
            if kwargs.get(name) is not None:
                raise CodexStateValidationError("Server-managed state is forbidden.")
        if kwargs.get("store") not in (None, False):
            raise CodexStateValidationError("Stateful Responses requests require store=false.")
        if kwargs.get("background") not in (None, False):
            raise CodexStateValidationError("Background Responses requests are forbidden.")
        for container_name in ("extra_body", "extra_request_body"):
            container = kwargs.get(container_name)
            if not isinstance(container, Mapping):
                continue
            if any(
                key in container and container.get(key) is not None
                for key in forbidden | {"background"}
            ) or container.get("store") not in (None, False):
                raise CodexStateValidationError("State request escape hatch is forbidden.")

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _prune_expired_chains_locked(self, now: datetime) -> None:
        expired = [
            chain_id
            for chain_id, entry in self._chain_registry.items()
            if not entry["in_flight"] and entry["expires_at"] <= now
        ]
        for chain_id in expired:
            del self._chain_registry[chain_id]

    def _enter_request(
        self,
        state: Optional[CodexResponsesState],
        tool_output_ids: List[str],
    ) -> None:
        with self._request_lock:
            now = self._now()
            self._prune_expired_chains_locked(now)
            if self._in_flight >= self._state_limits.max_concurrent_chains:
                raise CodexStateConcurrencyError("Codex state concurrency limit reached.")
            if state is None:
                if (
                    len(self._chain_registry) + self._initial_in_flight
                    >= self._state_limits.max_concurrent_chains
                ):
                    raise CodexStateConcurrencyError("Codex active chain limit reached.")
                self._initial_in_flight += 1
            else:
                entry = self._chain_registry.get(state.chain_id)
                if entry is not None and (
                    entry["in_flight"] or entry["generation"] != state.generation
                ):
                    if tool_output_ids:
                        raise CodexToolCallIntegrityError(
                            "Tool output belongs to a consumed state generation."
                        )
                    raise CodexStateGenerationError(
                        "Codex state generation is stale or already in flight."
                    )
                if entry is None:
                    if len(self._chain_registry) >= self._state_limits.max_concurrent_chains:
                        raise CodexStateConcurrencyError("Codex active chain limit reached.")
                    entry = {
                        "generation": state.generation,
                        "expires_at": state.expires_at,
                        "in_flight": False,
                    }
                    self._chain_registry[state.chain_id] = entry
                entry["in_flight"] = True
            self._in_flight += 1

    def _leave_request(
        self,
        old_state: Optional[CodexResponsesState],
        new_state: Optional[CodexResponsesState],
    ) -> None:
        with self._request_lock:
            if old_state is None:
                self._initial_in_flight -= 1
            else:
                entry = self._chain_registry.get(old_state.chain_id)
                if entry is not None:
                    entry["in_flight"] = False
            if new_state is not None:
                self._chain_registry[new_state.chain_id] = {
                    "generation": new_state.generation,
                    "expires_at": new_state.expires_at,
                    "in_flight": False,
                }
            self._in_flight -= 1

    def _measure_images(self, delta: List[Dict[str, Any]]) -> tuple[int, int]:
        count = 0
        total_bytes = 0
        for item in delta:
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "input_image":
                    continue
                count += 1
                value = str(part.get("image_url", ""))
                if value.startswith("data:") and "," in value:
                    encoded = value.split(",", 1)[1]
                    try:
                        size = len(base64.b64decode(encoded, validate=True))
                    except (ValueError, binascii.Error) as exc:
                        raise CodexStateValidationError("Image data URL is invalid.") from exc
                else:
                    raise CodexStateValidationError(
                        "Stateful Codex image inputs require bounded data URLs."
                    )
                total_bytes += size
        return count, total_bytes

    def _prepare_state_request(
        self,
        *,
        state: Optional[CodexResponsesState],
        expected_generation: Optional[int],
        kwargs: Dict[str, Any],
    ) -> tuple[
        Dict[str, Any],
        List[Dict[str, Any]],
        FrozenSet[str],
        int,
        int,
        int,
        datetime,
        str,
        str,
    ]:
        self._validate_forbidden_kwargs(kwargs)
        if self._origin != _APPROVED_CODEX_ORIGIN:
            raise CodexStateBindingError("OAuth Codex state origin is not approved.")
        if self._responses_compact_threshold is not None and not self._has_compaction_capability():
            raise CodexCapabilityError("Codex compaction capability is not verified.")
        if self._responses_compact_threshold is not None and self._responses_compact_threshold <= 0:
            raise CodexCapabilityError("Codex compaction threshold is invalid.")

        request_model = str(kwargs.get("model") or self._model)
        instructions = str(kwargs.get("instructions") or "You are a helpful assistant.")
        digest = _instructions_digest(instructions)
        now = self._now()
        prior_items: List[Dict[str, Any]] = []
        open_calls: set[str] = set()
        prior_turns = 0
        prior_images = 0
        prior_tool_bytes = 0

        if state is not None:
            self._validate_integrity(state)
            if now >= state.expires_at.astimezone(timezone.utc):
                raise CodexStateExpiredError("Codex state has expired.")
            if expected_generation is None or expected_generation != state.generation:
                raise CodexStateGenerationError("Codex state generation is stale or missing.")
            if (
                state.model != self._model
                or request_model != state.model
                or state.instructions_digest != digest
                or state.origin != self._origin
                or state.principal_fingerprint != self._principal_fingerprint
                or state.credential_fingerprint != self._credential_fingerprint
            ):
                raise CodexStateBindingError("Codex state binding changed.")
            if state.turn_count >= self._state_limits.max_turns:
                raise _limit_error("turn", self._state_limits.max_turns, state.turn_count + 1)
            prior_items = [_to_json_value(item) for item in state.response_items]
            open_calls = set(state.open_tool_call_ids)
            prior_turns = state.turn_count
            prior_images = state.image_count
            prior_tool_bytes = state.total_tool_output_bytes
            if len(state.seen_tool_call_ids) > self._state_limits.max_tool_call_ids:
                raise _limit_error(
                    "tool call",
                    self._state_limits.max_tool_call_ids,
                    len(state.seen_tool_call_ids),
                )
            for call_id in state.seen_tool_call_ids:
                call_id_bytes = len(call_id.encode("utf-8"))
                if call_id_bytes > self._state_limits.max_tool_call_id_bytes:
                    raise _limit_error(
                        "tool call ID bytes",
                        self._state_limits.max_tool_call_id_bytes,
                        call_id_bytes,
                    )
        elif expected_generation is not None:
            raise CodexStateGenerationError("Initial Codex state has no generation.")
        elif request_model != self._model:
            raise CodexStateBindingError("Codex state model binding changed.")

        messages = kwargs.get("messages") or [{"role": "user", "content": ""}]
        delta: List[Dict[str, Any]] = []
        tool_output_ids: List[str] = []
        turn_tool_bytes = 0
        for message in messages:
            if not isinstance(message, dict):
                raise CodexStateValidationError("Codex turn message is invalid.")
            role = str(message.get("role", "user") or "user")
            if role not in {"user", "tool"}:
                raise CodexStateValidationError(
                    "Stateful Codex turns accept only new user or tool messages."
                )
            converted = _convert_message_for_responses(message)
            if role == "tool":
                call_id = str(message.get("tool_call_id", "") or message.get("call_id", "") or "")
                if not call_id or call_id not in open_calls or call_id in tool_output_ids:
                    raise CodexToolCallIntegrityError("Tool output call ID is not open.")
                output_bytes = len(
                    _stringify_response_payload(message.get("content")).encode("utf-8")
                )
                if output_bytes > self._state_limits.max_tool_output_bytes:
                    raise _limit_error(
                        "tool output",
                        self._state_limits.max_tool_output_bytes,
                        output_bytes,
                    )
                turn_tool_bytes += output_bytes
                tool_output_ids.append(call_id)
            delta.extend(converted)

        total_tool_bytes = prior_tool_bytes + turn_tool_bytes
        if total_tool_bytes > self._state_limits.max_total_tool_output_bytes:
            raise _limit_error(
                "tool output",
                self._state_limits.max_total_tool_output_bytes,
                total_tool_bytes,
            )
        image_count, image_bytes = self._measure_images(delta)
        total_images = prior_images + image_count
        if total_images > self._state_limits.max_images:
            raise _limit_error("image", self._state_limits.max_images, total_images)
        if image_bytes > self._state_limits.max_image_bytes:
            raise _limit_error("image bytes", self._state_limits.max_image_bytes, image_bytes)

        tools = _convert_tools_for_responses(kwargs.get("tools"))
        preflight_items = [*prior_items, *delta]
        if len(preflight_items) > self._state_limits.max_items:
            raise _limit_error("item", self._state_limits.max_items, len(preflight_items))
        preflight_bytes = len(
            _canonical_json(
                {
                    "instructions": instructions,
                    "tools": tools or [],
                    "items": preflight_items,
                    "seen_tool_call_ids": (
                        sorted(state.seen_tool_call_ids) if state is not None else []
                    ),
                }
            ).encode("utf-8")
        )
        if preflight_bytes > self._state_limits.max_bytes:
            raise _limit_error("byte", self._state_limits.max_bytes, preflight_bytes)

        request: Dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": preflight_items,
            "store": False,
            "stream": True,
        }
        if tools:
            request["tools"] = tools
            if kwargs.get("tool_choice") is not None:
                request["tool_choice"] = kwargs["tool_choice"]
        if self._responses_compact_threshold is not None:
            request["context_management"] = [
                {
                    "type": "compaction",
                    "compact_threshold": self._responses_compact_threshold,
                }
            ]

        remaining_open = frozenset(open_calls.difference(tool_output_ids))
        expires_at = (
            state.expires_at
            if state is not None
            else now + timedelta(seconds=self._state_limits.ttl_seconds)
        )
        return (
            request,
            delta,
            remaining_open,
            prior_turns + 1,
            total_images,
            total_tool_bytes,
            expires_at,
            digest,
            request_model,
        )

    def _commit_state(
        self,
        *,
        state: Optional[CodexResponsesState],
        completed_response: Any,
        delta: List[Dict[str, Any]],
        remaining_open: FrozenSet[str],
        turn_count: int,
        image_count: int,
        total_tool_output_bytes: int,
        expires_at: datetime,
        instructions_digest: str,
        instructions: str,
        tools: Optional[List[Dict[str, Any]]],
        model: str,
    ) -> CodexResponsesTurn[Any]:
        if completed_response is None or _item_get(completed_response, "status") != "completed":
            raise CodexStateValidationError("Codex state requires an explicit completed response.")
        response_output = _item_get(completed_response, "output", None)
        if response_output is None:
            raise CodexStateValidationError("Completed Codex response is missing its full output.")
        raw_output = list(response_output)
        output_items = [_to_json_value(item) for item in raw_output]
        if any(not isinstance(item, dict) for item in output_items):
            raise CodexStateValidationError("Codex response output item is invalid.")

        prior_items = (
            [_to_json_value(item) for item in state.response_items] if state is not None else []
        )
        candidate = _latest_compaction_window([*prior_items, *delta, *output_items])
        if len(candidate) > self._state_limits.max_items:
            raise _limit_error("item", self._state_limits.max_items, len(candidate))

        open_calls = set(remaining_open)
        known_call_ids = set(state.seen_tool_call_ids) if state is not None else set()
        known_call_ids.update(
            {
                str(item.get("call_id"))
                for item in [*prior_items, *delta]
                if item.get("type") == "function_call" and item.get("call_id")
            }
        )
        for item in output_items:
            if item.get("type") != "function_call":
                continue
            call_id = str(item.get("call_id", "") or "")
            if not call_id or call_id in known_call_ids:
                raise CodexToolCallIntegrityError("Provider returned a duplicate tool call ID.")
            call_id_bytes = len(call_id.encode("utf-8"))
            if call_id_bytes > self._state_limits.max_tool_call_id_bytes:
                raise _limit_error(
                    "tool call ID bytes",
                    self._state_limits.max_tool_call_id_bytes,
                    call_id_bytes,
                )
            known_call_ids.add(call_id)
            open_calls.add(call_id)
        if len(known_call_ids) > self._state_limits.max_tool_call_ids:
            raise _limit_error(
                "tool call",
                self._state_limits.max_tool_call_ids,
                len(known_call_ids),
            )
        candidate_bytes = len(
            _canonical_json(
                {
                    "instructions": instructions,
                    "tools": tools or [],
                    "items": candidate,
                    "seen_tool_call_ids": sorted(known_call_ids),
                }
            ).encode("utf-8")
        )
        if candidate_bytes > self._state_limits.max_bytes:
            raise _limit_error("byte", self._state_limits.max_bytes, candidate_bytes)
        candidate_call_ids = {
            str(item.get("call_id"))
            for item in candidate
            if item.get("type") == "function_call" and item.get("call_id")
        }
        open_calls.intersection_update(candidate_call_ids)

        new_state = CodexResponsesState(
            chain_id=state.chain_id if state is not None else uuid.uuid4().hex,
            generation=state.generation + 1 if state is not None else 0,
            model=model,
            instructions_digest=instructions_digest,
            origin=self._origin,
            principal_fingerprint=self._principal_fingerprint,
            credential_fingerprint=self._credential_fingerprint,
            expires_at=expires_at,
            response_items=_freeze_items(candidate),
            open_tool_call_ids=frozenset(open_calls),
            turn_count=turn_count,
            image_count=image_count,
            total_tool_output_bytes=total_tool_output_bytes,
            seen_tool_call_ids=frozenset(known_call_ids),
        )
        signed = replace(
            new_state,
            integrity_tag=_state_tag(new_state, self._state_integrity_key),
        )
        visible = _build_chat_completion_like_response(completed_response, model)
        return CodexResponsesTurn(result=visible, state=signed)

    def create_with_state(
        self,
        *,
        state: Optional[CodexResponsesState] = None,
        expected_generation: Optional[int] = None,
        **kwargs: Any,
    ) -> CodexResponsesTurn[Any]:
        (
            request,
            delta,
            remaining_open,
            turn_count,
            image_count,
            total_tool_output_bytes,
            expires_at,
            digest,
            model,
        ) = self._prepare_state_request(
            state=state,
            expected_generation=expected_generation,
            kwargs=kwargs,
        )
        tool_output_ids = [
            item["call_id"] for item in delta if item.get("type") == "function_call_output"
        ]
        self._enter_request(state, tool_output_ids)
        published_state = None
        try:
            try:
                client = self._client_factory()
                try:
                    stream = client.responses.create(**request)
                    completed_response = None
                    completed_count = 0
                    try:
                        for event in stream:
                            event_type = _item_get(event, "type", "")
                            if event_type == "response.completed":
                                completed_count += 1
                                if completed_count > 1:
                                    raise CodexStateValidationError(
                                        "Codex stream emitted duplicate completion events."
                                    )
                                completed_response = _item_get(event, "response")
                    finally:
                        close = getattr(stream, "close", None)
                        if callable(close):
                            close()
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
                turn = self._commit_state(
                    state=state,
                    completed_response=completed_response,
                    delta=delta,
                    remaining_open=remaining_open,
                    turn_count=turn_count,
                    image_count=image_count,
                    total_tool_output_bytes=total_tool_output_bytes,
                    expires_at=expires_at,
                    instructions_digest=digest,
                    instructions=request["instructions"],
                    tools=request.get("tools"),
                    model=model,
                )
            except CodexStateValidationError:
                raise
            except Exception:
                raise CodexStateTransportError("Codex state transport failed.") from None
            published_state = turn.state
            return turn
        finally:
            self._leave_request(state, published_state)

    def probe_compaction_capability(
        self,
        *,
        probe_input: List[Dict[str, Any]],
    ) -> None:
        """Verify compaction emission and replay on this exact adapter binding."""
        threshold = self._responses_compact_threshold
        if threshold is None or threshold <= 0:
            raise CodexCapabilityError("Codex compaction threshold is not configured.")
        normalized_input = [_to_json_value(item) for item in probe_input]
        if not normalized_input or any(not isinstance(item, dict) for item in normalized_input):
            raise CodexCapabilityError("Codex compaction probe input is invalid.")
        probe_bytes = len(_canonical_json(normalized_input).encode("utf-8"))
        if (
            len(normalized_input) > self._state_limits.max_items
            or probe_bytes > self._state_limits.max_bytes
        ):
            raise CodexCapabilityError("Codex compaction probe input exceeds limits.")

        context_management = [{"type": "compaction", "compact_threshold": threshold}]

        def completed_output(input_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            request = {
                "model": self._model,
                "instructions": "Verify Responses compaction capability.",
                "input": input_items,
                "store": False,
                "stream": True,
                "context_management": context_management,
            }
            try:
                client = self._client_factory()
                try:
                    stream = client.responses.create(**request)
                    completed_response = None
                    completed_count = 0
                    try:
                        for event in stream:
                            if _item_get(event, "type", "") != "response.completed":
                                continue
                            completed_count += 1
                            if completed_count > 1:
                                raise CodexCapabilityError(
                                    "Codex compaction probe emitted duplicate completion."
                                )
                            completed_response = _item_get(event, "response")
                    finally:
                        close = getattr(stream, "close", None)
                        if callable(close):
                            close()
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
            except CodexStateValidationError:
                raise
            except Exception:
                raise CodexStateTransportError("Codex compaction probe transport failed.") from None
            if completed_response is None or _item_get(completed_response, "status") != "completed":
                raise CodexCapabilityError("Codex compaction probe did not complete.")
            raw_output = _item_get(completed_response, "output", None)
            if raw_output is None:
                raise CodexCapabilityError("Codex compaction probe returned no full output.")
            output = [_to_json_value(item) for item in raw_output]
            if any(not isinstance(item, dict) for item in output):
                raise CodexCapabilityError("Codex compaction probe output is invalid.")
            return output

        self._enter_request(None, [])
        try:
            first_output = completed_output(normalized_input)
            replay_window = _latest_compaction_window(first_output)
            if not replay_window or replay_window[0].get("type") != "compaction":
                raise CodexCapabilityError("Codex endpoint did not emit a compaction item.")
            completed_output(
                [
                    *replay_window,
                    {"role": "user", "content": "Verify compaction replay."},
                ]
            )
            self._compaction_capability_stamp = self._sign_compaction_capability()
        finally:
            self._leave_request(None, None)

    def fork_state(self, state: CodexResponsesState) -> CodexResponsesState:
        self._validate_integrity(state)
        if state.open_tool_call_ids:
            raise CodexToolCallIntegrityError("Codex state with open tool calls cannot be forked.")
        forked = replace(state, chain_id=uuid.uuid4().hex, integrity_tag="")
        signed = replace(
            forked,
            integrity_tag=_state_tag(forked, self._state_integrity_key),
        )
        with self._request_lock:
            now = self._now()
            self._prune_expired_chains_locked(now)
            if now >= state.expires_at.astimezone(timezone.utc):
                raise CodexStateExpiredError("Codex state has expired.")
            entry = self._chain_registry.get(state.chain_id)
            if entry is not None and (
                entry["in_flight"] or entry["generation"] != state.generation
            ):
                raise CodexStateGenerationError(
                    "Codex state generation is stale or already in flight."
                )
            if len(self._chain_registry) >= self._state_limits.max_concurrent_chains:
                raise CodexStateConcurrencyError("Codex active chain limit reached.")
            self._chain_registry[signed.chain_id] = {
                "generation": signed.generation,
                "expires_at": signed.expires_at,
                "in_flight": False,
            }
        return signed


class CodexChatShim:
    def __init__(self, adapter: CodexCompletionsAdapter):
        self.completions = adapter


class CodexAsyncCompletionsAdapter:
    def __init__(self, sync_adapter: CodexCompletionsAdapter):
        self._sync_adapter = sync_adapter

    async def create(self, **kwargs) -> Any:
        if kwargs.get("stream"):
            raise NotImplementedError("Streaming is not supported for openai-codex.")
        response = await asyncio.to_thread(self._sync_adapter._create_response, **kwargs)
        return response

    async def create_with_state(
        self,
        *,
        state: Optional[CodexResponsesState] = None,
        expected_generation: Optional[int] = None,
        **kwargs: Any,
    ) -> CodexResponsesTurn[Any]:
        (
            request,
            delta,
            remaining_open,
            turn_count,
            image_count,
            total_tool_output_bytes,
            expires_at,
            digest,
            model,
        ) = self._sync_adapter._prepare_state_request(
            state=state,
            expected_generation=expected_generation,
            kwargs=kwargs,
        )
        tool_output_ids = [
            item["call_id"] for item in delta if item.get("type") == "function_call_output"
        ]
        self._sync_adapter._enter_request(state, tool_output_ids)
        published_state = None
        try:
            try:
                if self._sync_adapter._async_client_factory is None:
                    raise CodexStateValidationError(
                        "Native async Codex Responses client is not configured."
                    )
                client = self._sync_adapter._async_client_factory()
                if asyncio.iscoroutine(client):
                    client = await client
                stream = None
                request_cancelled = False
                try:
                    stream = await client.responses.create(**request)
                    completed_response = None
                    completed_count = 0
                    async for event in stream:
                        event_type = _item_get(event, "type", "")
                        if event_type == "response.completed":
                            completed_count += 1
                            if completed_count > 1:
                                raise CodexStateValidationError(
                                    "Codex stream emitted duplicate completion events."
                                )
                            completed_response = _item_get(event, "response")
                except asyncio.CancelledError:
                    request_cancelled = True
                    raise
                finally:
                    try:
                        await _close_async_resources_cancellation_safe(stream, client)
                    except (Exception, asyncio.CancelledError):
                        if not request_cancelled:
                            raise
                turn = self._sync_adapter._commit_state(
                    state=state,
                    completed_response=completed_response,
                    delta=delta,
                    remaining_open=remaining_open,
                    turn_count=turn_count,
                    image_count=image_count,
                    total_tool_output_bytes=total_tool_output_bytes,
                    expires_at=expires_at,
                    instructions_digest=digest,
                    instructions=request["instructions"],
                    tools=request.get("tools"),
                    model=model,
                )
            except CodexStateValidationError:
                raise
            except Exception:
                raise CodexStateTransportError("Codex state transport failed.") from None
            published_state = turn.state
            return turn
        finally:
            self._sync_adapter._leave_request(state, published_state)


class CodexAsyncChatShim:
    def __init__(self, adapter: CodexAsyncCompletionsAdapter):
        self.completions = adapter
