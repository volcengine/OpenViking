# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Offline contracts for opt-in, caller-managed Codex Responses state.

The suite intentionally uses only fake Responses streams.  Its purpose is to
pin the commit boundary and security properties before production code exists.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace
from typing import Any, Iterable, Mapping
from unittest.mock import AsyncMock, patch

import pytest

from openviking.models.vlm.backends import codex_responses_adapter as state_api
from openviking.models.vlm.backends.codex_responses_adapter import (
    CodexAsyncCompletionsAdapter,
    CodexCompletionsAdapter,
)
from openviking.models.vlm.backends.codex_vlm import CodexVLM
from openviking.models.vlm.base import VLMBase
from openviking_cli.utils.config.vlm_config import VLMConfig

MODEL = "gpt-5.3-codex"
INSTRUCTIONS = "Keep the chain constraints."
APPROVED_ORIGIN = "https://chatgpt.com/backend-api/codex"
STATE_KEY = b"offline-test-state-integrity-key-32b"
PRINCIPAL = "principal:v1:test"
CREDENTIAL = "credential:v1:test"
OPAQUE_SECRET = "OPAQUE_STATE_SENTINEL_MUST_NOT_BE_LOGGED"


def _symbol(name: str) -> Any:
    value = getattr(state_api, name, None)
    assert value is not None, f"missing additive Codex Responses state API: {name}"
    return value


def _error(name: str) -> type[Exception]:
    value = _symbol(name)
    assert isinstance(value, type) and issubclass(value, Exception)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_thaw(item) for item in value)
    return value


def _canonical_items(state: Any) -> str:
    return json.dumps(_thaw(state.response_items), sort_keys=True, separators=(",", ":"))


def _message(text: str, *, secret: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }
    if secret is not None:
        item["opaque_provider_metadata"] = {"encrypted_content": secret}
    return item


def _reasoning(identifier: str = "reasoning-1") -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": identifier,
        "summary": [{"type": "summary_text", "text": "opaque reasoning summary"}],
        "encrypted_content": f"encrypted-{identifier}",
    }


def _compaction(identifier: str) -> dict[str, Any]:
    return {
        "type": "compaction",
        "id": identifier,
        "encrypted_content": f"encrypted-{identifier}",
    }


def _function_call(call_id: str, name: str = "lookup") -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": '{"query":"value"}',
    }


def _completed_event(items: list[dict[str, Any]]) -> SimpleNamespace:
    response = SimpleNamespace(
        status="completed",
        output=items,
        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
    )
    return SimpleNamespace(type="response.completed", response=response)


def _events(items: list[dict[str, Any]]) -> list[SimpleNamespace]:
    return [
        *[SimpleNamespace(type="response.output_item.done", item=item) for item in items],
        _completed_event(items),
    ]


class FakeSyncStream:
    def __init__(self, events: Iterable[Any], terminal_error: BaseException | None = None):
        self._events = list(events)
        self._terminal_error = terminal_error
        self.closed = False

    def __iter__(self):
        yield from self._events
        if self._terminal_error is not None:
            raise self._terminal_error

    def close(self) -> None:
        self.closed = True


class FakeResponses:
    def __init__(self, streams: Iterable[FakeSyncStream]):
        self._streams = deque(streams)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeSyncStream:
        self.calls.append(kwargs)
        assert self._streams, "test fake received an unexpected network call"
        return self._streams.popleft()


class FakeSyncClient:
    def __init__(self, streams: Iterable[FakeSyncStream]):
        self.responses = FakeResponses(streams)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class CountingFactory:
    def __init__(self, client: Any):
        self.client = client
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return self.client


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _limits(**overrides: int) -> Any:
    limits_type = _symbol("CodexResponsesLimits")
    values = {
        "max_bytes": 32 * 1024 * 1024,
        "max_items": 4096,
        "max_turns": 256,
        "max_images": 8,
        "max_image_bytes": 8 * 1024 * 1024,
        "max_tool_output_bytes": 1024 * 1024,
        "max_total_tool_output_bytes": 4 * 1024 * 1024,
        "max_tool_call_ids": 4096,
        "max_tool_call_id_bytes": 512,
        "ttl_seconds": 3600,
        "max_concurrent_chains": 16,
    }
    values.update(overrides)
    return limits_type(**values)


def _adapter(
    client: Any,
    *,
    factory: CountingFactory | None = None,
    async_client_factory: Any = None,
    clock: MutableClock | None = None,
    limits: Any = None,
    model: str = MODEL,
    origin: str = APPROVED_ORIGIN,
    principal: str = PRINCIPAL,
    credential: str = CREDENTIAL,
    state_key: bytes = STATE_KEY,
    compact_threshold: int | None = None,
    capability_verified: bool = False,
) -> tuple[CodexCompletionsAdapter, CountingFactory]:
    client_factory = factory or CountingFactory(client)
    adapter = CodexCompletionsAdapter(
        client_factory,
        model,
        async_client_factory=async_client_factory,
        state_integrity_key=state_key,
        origin=origin,
        principal_fingerprint=principal,
        credential_fingerprint=credential,
        state_limits=limits or _limits(),
        clock=clock or MutableClock(),
        responses_compact_threshold=compact_threshold,
    )
    if capability_verified:
        adapter._compaction_capability_stamp = adapter._sign_compaction_capability()
    return adapter, client_factory


def _turn(
    adapter: CodexCompletionsAdapter,
    text: str,
    *,
    state: Any = None,
    expected_generation: int | None = None,
    instructions: str = INSTRUCTIONS,
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> Any:
    return adapter.create_with_state(
        state=state,
        expected_generation=expected_generation,
        model=MODEL,
        instructions=instructions,
        messages=messages or [{"role": "user", "content": text}],
        **kwargs,
    )


def _initial_state(
    items: list[dict[str, Any]],
    *,
    client: FakeSyncClient | None = None,
    **adapter_kwargs: Any,
) -> tuple[CodexCompletionsAdapter, Any, FakeSyncClient, CountingFactory]:
    fake_client = client or FakeSyncClient([FakeSyncStream(_events(items))])
    adapter, factory = _adapter(fake_client, **adapter_kwargs)
    turn = _turn(adapter, "initial")
    return adapter, turn.state, fake_client, factory


def test_state_api_is_additive_and_does_not_expand_vlm_base():
    """Only the Codex provider may expose caller-managed Responses state."""
    required = {
        "CodexResponsesState",
        "CodexResponsesTurn",
        "CodexResponsesLimits",
        "CodexStateValidationError",
        "CodexStateExpiredError",
        "CodexStateGenerationError",
        "CodexStateBindingError",
        "CodexToolCallIntegrityError",
        "CodexStateLimitError",
        "CodexCapabilityError",
        "CodexStateConcurrencyError",
        "CodexStateTransportError",
    }

    assert required <= set(vars(state_api))
    assert hasattr(CodexVLM, "get_completion_with_state")
    assert hasattr(CodexVLM, "get_completion_with_state_async")
    assert not hasattr(VLMBase, "get_completion_with_state")
    assert not hasattr(VLMBase, "get_completion_with_state_async")


def test_state_and_turn_are_frozen_value_objects():
    """Caller-managed branching is safe only when published objects cannot mutate."""
    client = FakeSyncClient([FakeSyncStream(_events([_message("done")]))])
    adapter, _factory = _adapter(client)
    turn = _turn(adapter, "initial")

    with pytest.raises(FrozenInstanceError):
        turn.state = None
    with pytest.raises(FrozenInstanceError):
        turn.state.generation = 99


def test_reasoning_and_unknown_opaque_fields_are_preserved_in_canonical_ledger():
    """The full ledger keeps the input delta and every provider output item."""
    reasoning = _reasoning()
    message = _message("visible", secret=OPAQUE_SECRET)
    _adapter_instance, state, _client, _factory = _initial_state([reasoning, message])

    assert _thaw(state.response_items) == [
        {"role": "user", "content": "initial"},
        reasoning,
        message,
    ]
    assert state.generation == 0


def test_nested_response_items_are_deeply_immutable_and_defensively_copied():
    """Frozen outer dataclasses are insufficient if nested provider dicts can change."""
    source_item = _reasoning()
    _adapter_instance, state, _client, _factory = _initial_state([source_item])
    before = _canonical_items(state)
    source_item["summary"][0]["text"] = "mutated source"

    assert _canonical_items(state) == before
    with pytest.raises((TypeError, AttributeError)):
        state.response_items[1]["summary"][0]["text"] = "mutated published state"
    assert _canonical_items(state) == before


def test_followup_request_replays_full_prior_ledger_before_only_the_new_delta():
    """Manual stateless chaining requires every prior output item in original order."""
    first_items = [_reasoning(), _message("first")]
    second_items = [_reasoning("reasoning-2"), _message("second")]
    client = FakeSyncClient(
        [FakeSyncStream(_events(first_items)), FakeSyncStream(_events(second_items))]
    )
    adapter, _factory = _adapter(client)
    first = _turn(adapter, "first")

    second = _turn(
        adapter,
        "new delta only",
        state=first.state,
        expected_generation=first.state.generation,
    )

    assert client.responses.calls[1]["input"] == [
        {"role": "user", "content": "first"},
        *first_items,
        {"role": "user", "content": "new delta only"},
    ]
    assert _thaw(second.state.response_items) == [
        {"role": "user", "content": "first"},
        *first_items,
        {"role": "user", "content": "new delta only"},
        *second_items,
    ]
    assert second.state.generation == first.state.generation + 1


def test_branching_from_one_immutable_state_keeps_both_children_isolated():
    """Branching is explicit: each fork gets a new chain identity and immutable ledger."""
    parent_item = _message("parent")
    left_item = _message("left")
    right_item = _message("right")
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([parent_item])),
            FakeSyncStream(_events([left_item])),
            FakeSyncStream(_events([right_item])),
        ]
    )
    adapter, _factory = _adapter(client)
    parent = _turn(adapter, "root")
    parent_before = _canonical_items(parent.state)
    left_seed = adapter.fork_state(parent.state)
    right_seed = adapter.fork_state(parent.state)

    left = _turn(
        adapter,
        "left delta",
        state=left_seed,
        expected_generation=left_seed.generation,
    )
    right = _turn(
        adapter,
        "right delta",
        state=right_seed,
        expected_generation=right_seed.generation,
    )

    assert _canonical_items(parent.state) == parent_before
    assert len({parent.state.chain_id, left.state.chain_id, right.state.chain_id}) == 3
    assert _canonical_items(left.state) != _canonical_items(right.state)


def test_fork_with_open_tool_calls_is_rejected():
    """A call capability cannot be duplicated into two independently consumable chains."""
    adapter, state, _client, _factory = _initial_state([_function_call("call-1")])

    with pytest.raises(_error("CodexToolCallIntegrityError")):
        adapter.fork_state(state)


def test_parallel_initial_chains_never_share_items_or_chain_ids():
    """Instance-local concurrency must not imply a shared transcript buffer."""

    class DynamicResponses:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []
            self._lock = threading.Lock()

        def create(self, **kwargs: Any) -> FakeSyncStream:
            with self._lock:
                self.calls.append(kwargs)
            text = str(kwargs["input"][-1]["content"])
            return FakeSyncStream(_events([_message(f"reply:{text}")]))

    client = SimpleNamespace(responses=DynamicResponses())
    adapter, _factory = _adapter(client)

    with ThreadPoolExecutor(max_workers=4) as pool:
        turns = list(pool.map(lambda index: _turn(adapter, f"chain-{index}"), range(8)))

    assert len({turn.state.chain_id for turn in turns}) == len(turns)
    for index, turn in enumerate(turns):
        ledger = _canonical_items(turn.state)
        assert f"reply:chain-{index}" in ledger
        assert all(f"reply:chain-{other}" not in ledger for other in range(8) if other != index)


def test_only_items_before_the_newest_compaction_item_are_pruned():
    """Pruning at an older compaction point would replay content the provider superseded."""
    old = _message("old")
    older_compaction = _compaction("compact-1")
    middle = _message("middle")
    newest_compaction = _compaction("compact-2")
    tail = _message("tail")
    _adapter_instance, state, _client, _factory = _initial_state(
        [old, older_compaction, middle, newest_compaction, tail]
    )

    assert _thaw(state.response_items) == [newest_compaction, tail]


def test_no_compaction_item_means_no_pruning():
    """Normal input/output history remains lossless when compaction did not occur."""
    items = [_reasoning(), _message("one"), _function_call("call-1")]
    _adapter_instance, state, _client, _factory = _initial_state(items)

    assert _thaw(state.response_items) == [
        {"role": "user", "content": "initial"},
        *items,
    ]


@pytest.mark.parametrize(
    ("adapter_overrides", "instructions"),
    [
        ({"model": "different-model"}, INSTRUCTIONS),
        ({"origin": "https://chatgpt.com/backend-api/other"}, INSTRUCTIONS),
        ({"principal": "principal:v1:other"}, INSTRUCTIONS),
        ({"credential": "credential:v1:other"}, INSTRUCTIONS),
        ({}, "changed instructions"),
    ],
)
def test_binding_changes_fail_before_client_creation(
    adapter_overrides: dict[str, Any],
    instructions: str,
):
    """A chain may not silently change model, instructions, origin, principal, or credential."""
    source_client = FakeSyncClient([FakeSyncStream(_events([_message("initial")]))])
    _source_adapter, state, _client, _factory = _initial_state(
        [_message("initial")], client=source_client
    )
    target_client = FakeSyncClient([])
    target_adapter, target_factory = _adapter(target_client, **adapter_overrides)

    with pytest.raises(_error("CodexStateBindingError")):
        _turn(
            target_adapter,
            "next",
            state=state,
            expected_generation=state.generation,
            instructions=instructions,
        )

    assert target_factory.calls == 0


def test_origin_is_canonicalized_but_custom_oauth_origin_is_rejected():
    """OAuth credentials are restricted to the one approved HTTPS Codex origin."""
    canonical_client = FakeSyncClient([FakeSyncStream(_events([_message("ok")]))])
    canonical_adapter, _factory = _adapter(
        canonical_client,
        origin="HTTPS://CHATGPT.COM:443/backend-api/codex/",
    )
    turn = _turn(canonical_adapter, "initial")
    assert turn.state.origin == APPROVED_ORIGIN

    custom_client = FakeSyncClient([])
    custom_adapter, custom_factory = _adapter(
        custom_client,
        origin="https://codex-proxy.example.test/v1",
    )
    with pytest.raises(_error("CodexStateBindingError")):
        _turn(custom_adapter, "must not send")
    assert custom_factory.calls == 0


def test_stale_or_missing_expected_generation_fails_before_network():
    """Explicit generation checks prevent accidental replay of a stale branch."""
    source_adapter, state, _source_client, _source_factory = _initial_state([_message("initial")])
    target_client = FakeSyncClient([])
    target_adapter, target_factory = _adapter(target_client)

    for expected_generation in (None, state.generation + 1):
        with pytest.raises(_error("CodexStateGenerationError")):
            _turn(
                target_adapter,
                "next",
                state=state,
                expected_generation=expected_generation,
            )

    assert source_adapter is not None
    assert target_factory.calls == 0


def test_forged_state_mac_fails_before_network():
    """Caller-visible fields cannot be edited and re-signed implicitly."""
    _adapter_instance, state, _client, _factory = _initial_state([_message("initial")])
    forged_items = (MappingProxyType(_message("forged")),)
    forged_state = replace(state, response_items=forged_items)
    target_client = FakeSyncClient([])
    target_adapter, target_factory = _adapter(target_client)

    with pytest.raises(_error("CodexStateValidationError"), match="integrity"):
        _turn(
            target_adapter,
            "next",
            state=forged_state,
            expected_generation=forged_state.generation,
        )

    assert target_factory.calls == 0


def test_function_call_is_open_until_one_matching_local_tool_output_closes_it():
    """Tool output is accepted once and only for an open call in this chain generation."""
    call = _function_call("call-1")
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([call])),
            FakeSyncStream(_events([_message("tool consumed")])),
        ]
    )
    adapter, _factory = _adapter(client)
    first = _turn(adapter, "use tool")
    assert first.state.open_tool_call_ids == frozenset({"call-1"})

    second = _turn(
        adapter,
        "",
        state=first.state,
        expected_generation=first.state.generation,
        messages=[
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"result":"ok"}',
            }
        ],
    )

    assert second.state.open_tool_call_ids == frozenset()
    assert client.responses.calls[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"result":"ok"}',
    }

    with pytest.raises(_error("CodexToolCallIntegrityError")):
        _turn(
            adapter,
            "",
            state=first.state,
            expected_generation=first.state.generation,
            messages=[
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": '{"result":"duplicate"}',
                }
            ],
        )
    assert len(client.responses.calls) == 2


def test_failed_tool_output_turn_releases_lease_and_keeps_call_open_for_retry():
    """A failed provider turn must not consume the caller's local tool capability."""
    call = _function_call("call-retry")
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([call])),
            FakeSyncStream([], terminal_error=TimeoutError(OPAQUE_SECRET)),
            FakeSyncStream(_events([_message("retry accepted")])),
        ]
    )
    adapter, _factory = _adapter(client)
    first = _turn(adapter, "use tool")
    tool_delta = [
        {
            "role": "tool",
            "tool_call_id": "call-retry",
            "content": '{"result":"safe"}',
        }
    ]

    with pytest.raises(_error("CodexStateTransportError")):
        _turn(
            adapter,
            "",
            state=first.state,
            expected_generation=first.state.generation,
            messages=tool_delta,
        )

    retried = _turn(
        adapter,
        "",
        state=first.state,
        expected_generation=first.state.generation,
        messages=tool_delta,
    )
    assert retried.state.open_tool_call_ids == frozenset()
    assert len(client.responses.calls) == 3


def test_successful_followup_makes_the_consumed_generation_stale():
    """A published successor must prevent replaying its parent generation."""
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([_message("first")])),
            FakeSyncStream(_events([_message("second")])),
        ]
    )
    adapter, factory = _adapter(client)
    parent = _turn(adapter, "first")
    _turn(
        adapter,
        "second",
        state=parent.state,
        expected_generation=parent.state.generation,
    )

    with pytest.raises(_error("CodexStateGenerationError")):
        _turn(
            adapter,
            "replay parent",
            state=parent.state,
            expected_generation=parent.state.generation,
        )

    assert factory.calls == 2


def test_consumed_generation_cannot_be_forked_after_successor_is_published():
    """Forking must honor the same stale-generation boundary as network turns."""
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([_message("parent")])),
            FakeSyncStream(_events([_message("child")])),
        ]
    )
    adapter, _factory = _adapter(client)
    parent = _turn(adapter, "parent")
    _turn(
        adapter,
        "child",
        state=parent.state,
        expected_generation=parent.state.generation,
    )

    with pytest.raises(_error("CodexStateGenerationError")):
        adapter.fork_state(parent.state)


@pytest.mark.parametrize("call_id", ["unknown-call", "call-from-another-chain"])
def test_unknown_or_cross_chain_tool_output_fails_before_network(call_id: str):
    """Provider call IDs are capabilities scoped to their originating chain."""
    _adapter_instance, state, _client, _factory = _initial_state([_function_call("known-call")])
    target_client = FakeSyncClient([])
    target_adapter, target_factory = _adapter(target_client)

    with pytest.raises(_error("CodexToolCallIntegrityError")):
        _turn(
            target_adapter,
            "",
            state=state,
            expected_generation=state.generation,
            messages=[{"role": "tool", "tool_call_id": call_id, "content": "result"}],
        )

    assert target_factory.calls == 0


@pytest.mark.parametrize(
    "events",
    [
        [SimpleNamespace(type="response.output_item.done", item=_message("partial"))],
        [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(status="incomplete", output=[_message("partial")]),
            )
        ],
        [SimpleNamespace(type="response.incomplete", response=None)],
    ],
)
def test_partial_or_noncompleted_stream_never_publishes_candidate_state(events: list[Any]):
    """Only an explicit completed response is a commit record."""
    initial_item = _message("initial")
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([initial_item])),
            FakeSyncStream(events),
        ]
    )
    adapter, _factory = _adapter(client)
    first = _turn(adapter, "initial")
    before = _canonical_items(first.state)

    with pytest.raises(_error("CodexStateValidationError")):
        _turn(
            adapter,
            "next",
            state=first.state,
            expected_generation=first.state.generation,
        )

    assert _canonical_items(first.state) == before


def test_timeout_after_first_event_is_redacted_closes_stream_and_does_not_retry():
    """Transport failures must preserve state without reflecting provider-controlled text."""
    initial_item = _message("initial")
    failing_stream = FakeSyncStream(
        [SimpleNamespace(type="response.output_item.done", item=_message("candidate"))],
        terminal_error=TimeoutError(OPAQUE_SECRET),
    )
    client = FakeSyncClient([FakeSyncStream(_events([initial_item])), failing_stream])
    adapter, factory = _adapter(client)
    first = _turn(adapter, "initial")
    before = _canonical_items(first.state)

    with pytest.raises(_error("CodexStateTransportError")) as exc_info:
        _turn(
            adapter,
            "next",
            state=first.state,
            expected_generation=first.state.generation,
        )

    assert OPAQUE_SECRET not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert failing_stream.closed is True
    assert _canonical_items(first.state) == before
    assert factory.calls == 2
    assert len(client.responses.calls) == 2


def test_state_request_forces_store_false_and_forbids_server_managed_state():
    """Caller-managed state must not accidentally mix with Conversations or response IDs."""
    client = FakeSyncClient([FakeSyncStream(_events([_message("done")]))])
    adapter, _factory = _adapter(client)

    _turn(adapter, "initial")

    request = client.responses.calls[0]
    assert request["store"] is False
    assert request["stream"] is True
    assert "conversation" not in request
    assert "previous_response_id" not in request


@pytest.mark.parametrize(
    "forbidden_kwargs",
    [
        {"store": True},
        {"conversation": "conv-secret"},
        {"previous_response_id": "resp-secret"},
        {"background": True},
        {"extra_body": {"previous_response_id": "resp-secret"}},
        {"extra_request_body": {"conversation": "conv-secret"}},
    ],
)
def test_forbidden_state_kwargs_fail_before_network(forbidden_kwargs: dict[str, Any]):
    """Escape hatches may not override the local-state privacy contract."""
    client = FakeSyncClient([])
    adapter, factory = _adapter(client)

    with pytest.raises(_error("CodexStateValidationError")):
        _turn(adapter, "initial", **forbidden_kwargs)

    assert factory.calls == 0


def test_compaction_is_opt_in_capability_gated_and_has_no_silent_fallback():
    """Unsupported context management must fail, not retry without compaction."""
    blocked_client = FakeSyncClient([])
    blocked_adapter, blocked_factory = _adapter(
        blocked_client,
        compact_threshold=200_000,
        capability_verified=False,
    )
    with pytest.raises(_error("CodexCapabilityError")):
        _turn(blocked_adapter, "initial")
    assert blocked_factory.calls == 0

    enabled_client = FakeSyncClient([FakeSyncStream(_events([_message("done")]))])
    enabled_adapter, _factory = _adapter(
        enabled_client,
        compact_threshold=200_000,
        capability_verified=True,
    )
    _turn(enabled_adapter, "initial")
    assert enabled_client.responses.calls[0]["context_management"] == [
        {"type": "compaction", "compact_threshold": 200_000}
    ]

    disabled_client = FakeSyncClient([FakeSyncStream(_events([_message("done")]))])
    disabled_adapter, _factory = _adapter(disabled_client)
    _turn(disabled_adapter, "initial")
    assert "context_management" not in disabled_client.responses.calls[0]


def test_capability_probe_requires_real_compaction_and_replay_before_enabling():
    """Parameter acceptance alone is insufficient; the opaque item must replay."""
    compaction = _compaction("probe-compaction")
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([_message("before"), compaction, _message("after")])),
            FakeSyncStream(_events([_message("replay completed")])),
            FakeSyncStream(_events([_message("state turn")])),
        ]
    )
    adapter, _factory = _adapter(
        client,
        compact_threshold=200_000,
        capability_verified=False,
    )

    adapter.probe_compaction_capability(
        probe_input=[{"role": "user", "content": "sanitized long probe fixture"}]
    )
    _turn(adapter, "enabled after probe")

    assert len(client.responses.calls) == 3
    assert client.responses.calls[0]["store"] is False
    assert client.responses.calls[1]["input"] == [
        compaction,
        _message("after"),
        {"role": "user", "content": "Verify compaction replay."},
    ]
    assert all(
        call["context_management"] == [{"type": "compaction", "compact_threshold": 200_000}]
        for call in client.responses.calls
    )


def test_capability_probe_without_compaction_fails_without_fallback_or_stamp():
    """An unsupported endpoint must remain fail-closed after one probe request."""
    client = FakeSyncClient([FakeSyncStream(_events([_message("no compaction")]))])
    adapter, factory = _adapter(
        client,
        compact_threshold=200_000,
        capability_verified=False,
    )

    with pytest.raises(_error("CodexCapabilityError")):
        adapter.probe_compaction_capability(
            probe_input=[{"role": "user", "content": "too short or unsupported"}]
        )
    with pytest.raises(_error("CodexCapabilityError")):
        _turn(adapter, "still blocked")

    assert factory.calls == 1
    assert len(client.responses.calls) == 1
    assert client.close_calls == 1


def test_capability_cannot_be_enabled_through_constructor_bypass():
    """Only the endpoint-bound probe may mint a production capability stamp."""
    assert (
        "compaction_capability_verified"
        not in inspect.signature(CodexCompletionsAdapter).parameters
    )


@patch("openviking.models.vlm.backends.codex_vlm.httpx.Client")
@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
def test_state_sync_client_disables_redirects(mock_openai, mock_httpx_client):
    """OAuth bearer credentials must not follow a redirect to another origin."""
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "api_key": "explicit-test-key",
            "api_base": APPROVED_ORIGIN,
            "responses_state_enabled": True,
        }
    )

    vlm._build_state_responses_client("explicit-test-key", APPROVED_ORIGIN)

    mock_httpx_client.assert_called_once_with(
        follow_redirects=False,
        timeout=vlm.timeout,
    )
    assert mock_openai.call_args.kwargs["http_client"] is mock_httpx_client.return_value


@patch.object(CodexCompletionsAdapter, "probe_compaction_capability")
def test_vlm_exposes_explicit_non_automatic_capability_probe(mock_probe):
    """The potentially billable probe runs only through an explicit caller action."""
    probe_input = [{"role": "user", "content": "sanitized probe"}]
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "api_key": "explicit-test-key",
            "api_base": APPROVED_ORIGIN,
            "responses_state_enabled": True,
            "responses_compact_threshold": 200_000,
        }
    )

    vlm.probe_responses_compaction_capability(probe_input=probe_input)

    vlm._get_or_create_state_adapters()
    mock_probe.assert_called_once_with(probe_input=probe_input)


def test_vlm_config_propagates_codex_state_opt_in_without_enabling_compaction():
    """The supported config path must preserve the explicit pilot controls."""
    config = VLMConfig(
        provider="openai-codex",
        model=MODEL,
        api_key="explicit-test-key",
        responses_state_enabled=True,
        responses_compact_threshold=200_000,
    )

    vlm = config.get_vlm_instance()

    assert isinstance(vlm, CodexVLM)
    assert vlm.config["responses_state_enabled"] is True
    assert vlm.config["responses_compact_threshold"] == 200_000
    adapter, _async_adapter = vlm._get_or_create_state_adapters()
    assert adapter._has_compaction_capability() is False


@pytest.mark.parametrize(
    "config_kwargs",
    [
        {
            "provider": "openai",
            "api_key": "explicit-test-key",
            "responses_state_enabled": True,
        },
        {
            "provider": "openai-codex",
            "api_key": "explicit-test-key",
            "responses_compact_threshold": 200_000,
        },
        {
            "model": MODEL,
            "responses_state_enabled": True,
            "credentials": [
                {
                    "provider": "openai-codex",
                    "api_key": "first",
                },
                {
                    "provider": "openai-codex",
                    "api_key": "second",
                },
            ],
        },
    ],
)
def test_vlm_config_rejects_state_outside_single_codex_pilot(
    config_kwargs: dict[str, Any],
):
    """State mode may not silently enter another provider or failover path."""
    values = dict(config_kwargs)
    model = values.pop("model", MODEL)
    with pytest.raises(ValueError, match="Responses state"):
        VLMConfig(model=model, **values)


@pytest.mark.parametrize(
    ("limit_overrides", "input_messages", "output_items"),
    [
        ({"max_bytes": 100}, None, [_message("x" * 500)]),
        ({"max_items": 1}, None, [_reasoning(), _message("two items")]),
        (
            {"max_images": 1},
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,BB=="}},
                    ],
                }
            ],
            [_message("images")],
        ),
        (
            {"max_image_bytes": 4},
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,QUJDREU="},
                        }
                    ],
                }
            ],
            [_message("image bytes")],
        ),
    ],
)
def test_state_byte_item_and_image_limits_fail_loud_without_partial_publication(
    limit_overrides: dict[str, int],
    input_messages: list[dict[str, Any]] | None,
    output_items: list[dict[str, Any]],
):
    """Bounded state prevents an opt-in chain from becoming an unbounded memory sink."""
    client = FakeSyncClient([FakeSyncStream(_events(output_items))])
    adapter, _factory = _adapter(client, limits=_limits(**limit_overrides))

    with pytest.raises(_error("CodexStateLimitError")) as exc_info:
        _turn(adapter, "initial", messages=input_messages)

    message = str(exc_info.value)
    assert OPAQUE_SECRET not in message
    assert len(client.responses.calls) <= 1


def test_turn_limit_is_checked_before_followup_network_access():
    """A chain over its reviewed turn budget must compact or stop explicitly."""
    source_adapter, state, _client, _factory = _initial_state(
        [_message("initial")],
        limits=_limits(max_turns=1),
    )
    target_client = FakeSyncClient([])
    target_adapter, target_factory = _adapter(
        target_client,
        limits=_limits(max_turns=1),
    )

    with pytest.raises(_error("CodexStateLimitError"), match="turn"):
        _turn(
            target_adapter,
            "next",
            state=state,
            expected_generation=state.generation,
        )

    assert source_adapter is not None
    assert target_factory.calls == 0


def test_tool_output_per_item_and_per_chain_limits_are_preflight_checks():
    """Oversized tool results are rejected before credentials or network are touched."""
    calls = [_function_call("call-1"), _function_call("call-2")]
    _adapter_instance, state, _client, _factory = _initial_state(calls)

    cases = [
        (
            _limits(max_tool_output_bytes=4),
            [{"role": "tool", "tool_call_id": "call-1", "content": "12345"}],
        ),
        (
            _limits(max_tool_output_bytes=10, max_total_tool_output_bytes=8),
            [
                {"role": "tool", "tool_call_id": "call-1", "content": "12345"},
                {"role": "tool", "tool_call_id": "call-2", "content": "67890"},
            ],
        ),
    ]
    for limits, messages in cases:
        target_client = FakeSyncClient([])
        target_adapter, target_factory = _adapter(target_client, limits=limits)
        with pytest.raises(_error("CodexStateLimitError"), match="tool"):
            _turn(
                target_adapter,
                "",
                state=state,
                expected_generation=state.generation,
                messages=messages,
            )
        assert target_factory.calls == 0


def test_compaction_does_not_remove_historical_tool_call_limits():
    """Pruned ledger items may not turn retained replay IDs into an unbounded side table."""
    client = FakeSyncClient(
        [
            FakeSyncStream(_events([_function_call("call-1")])),
            FakeSyncStream(
                _events(
                    [
                        _compaction("compact-tools"),
                        _function_call("call-2"),
                    ]
                )
            ),
        ]
    )
    adapter, _factory = _adapter(
        client,
        limits=_limits(max_tool_call_ids=1),
    )
    first = _turn(adapter, "initial")

    with pytest.raises(_error("CodexStateLimitError"), match="tool call"):
        _turn(
            adapter,
            "",
            state=first.state,
            expected_generation=first.state.generation,
            messages=[
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "done",
                }
            ],
        )

    assert first.state.generation == 0


def test_provider_tool_call_id_has_a_hard_byte_limit():
    """Provider-controlled call IDs are retained state and must be individually bounded."""
    client = FakeSyncClient([FakeSyncStream(_events([_function_call("x" * 9)]))])
    adapter, _factory = _adapter(
        client,
        limits=_limits(max_tool_call_id_bytes=8),
    )

    with pytest.raises(_error("CodexStateLimitError"), match="tool call ID"):
        _turn(adapter, "initial")


def test_retained_tool_call_limit_is_rechecked_before_network():
    """A stricter adapter must reject oversized replay metadata before transport."""
    _source, state, _client, _factory = _initial_state(
        [_function_call("call-1"), _function_call("call-2")]
    )
    target_client = FakeSyncClient([])
    target, target_factory = _adapter(
        target_client,
        limits=_limits(max_tool_call_ids=1),
    )

    with pytest.raises(_error("CodexStateLimitError"), match="tool call"):
        _turn(
            target,
            "next",
            state=state,
            expected_generation=state.generation,
        )

    assert target_factory.calls == 0


def test_expired_state_fails_before_client_creation():
    """TTL is a hard replay boundary, not advisory metadata."""
    clock = MutableClock()
    _adapter_instance, state, _client, _factory = _initial_state([_message("initial")], clock=clock)
    clock.value += timedelta(seconds=3601)
    target_client = FakeSyncClient([])
    target_adapter, target_factory = _adapter(target_client, clock=clock)

    with pytest.raises(_error("CodexStateExpiredError")):
        _turn(
            target_adapter,
            "next",
            state=state,
            expected_generation=state.generation,
        )

    assert target_factory.calls == 0


def test_state_expires_at_the_exact_ttl_boundary():
    """The expiry instant is excluded so there is no one-tick replay window."""
    clock = MutableClock()
    client = FakeSyncClient([FakeSyncStream(_events([_message("initial")]))])
    adapter, factory = _adapter(client, clock=clock)
    state = _turn(adapter, "initial").state
    clock.value += timedelta(seconds=3600)

    with pytest.raises(_error("CodexStateExpiredError")):
        _turn(
            adapter,
            "expired",
            state=state,
            expected_generation=state.generation,
        )

    assert factory.calls == 1


@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_oauth_state_mode_requires_stable_principal_claims(mock_resolve):
    """A credential slot alone cannot distinguish an account replacement."""
    mock_resolve.return_value = {
        "api_key": "opaque-access-token-without-jwt-claims",
        "base_url": APPROVED_ORIGIN,
        "auth_owner": "external",
        "source": "codex-cli",
        "path": "/redacted/auth-slot",
    }
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "responses_state_enabled": True,
        }
    )

    with pytest.raises(_error("CodexStateBindingError")):
        vlm._get_or_create_state_adapters()


def test_oauth_credential_slot_is_stable_without_client_id_across_owner_transition():
    """A refresh takeover must not invalidate the same persistent credential slot."""
    token = "eyJhbGciOiJub25lIn0.eyJpc3MiOiJodHRwczovL2F1dGgub3BlbmFpLmNvbSIsInN1YiI6InVzZXItMSJ9."
    external = {
        "api_key": token,
        "base_url": APPROVED_ORIGIN,
        "auth_owner": "external",
        "source": "codex-cli",
        "path": "/redacted/persistent-auth-slot",
    }
    taken_over = {
        **external,
        "auth_owner": "openviking",
        "source": "openviking",
    }
    different_slot = {
        **taken_over,
        "path": "/redacted/different-auth-slot",
    }

    first = CodexVLM._oauth_state_bindings(external, "")
    second = CodexVLM._oauth_state_bindings(taken_over, "")
    other = CodexVLM._oauth_state_bindings(different_slot, "")

    assert first[3] == second[3]
    assert first[3] != other[3]


@pytest.mark.parametrize(
    "unsafe_config",
    [
        {"extra_headers": {"x-pilot": "not-reviewed"}},
        {"extra_request_body": {"metadata": {"secret": OPAQUE_SECRET}}},
    ],
)
def test_vlm_state_pilot_rejects_custom_request_escape_hatches(unsafe_config):
    """The OAuth pilot must not grow unreviewed headers or request-body fields."""
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "api_key": "explicit-test-key",
            "responses_state_enabled": True,
            **unsafe_config,
        }
    )

    with pytest.raises(_error("CodexStateValidationError")):
        vlm._get_or_create_state_adapters()


class FakeAsyncStream:
    def __init__(
        self,
        events: Iterable[Any],
        *,
        terminal_error: BaseException | None = None,
        entered: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ):
        self._events = deque(events)
        self._terminal_error = terminal_error
        self._entered = entered
        self._release = release
        self.closed = False
        self._started = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._started:
            self._started = True
            if self._entered is not None:
                self._entered.set()
            if self._release is not None:
                await self._release.wait()
        if self._events:
            return self._events.popleft()
        if self._terminal_error is not None:
            error = self._terminal_error
            self._terminal_error = None
            raise error
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


class FakeAsyncResponses:
    def __init__(self, streams: Iterable[FakeAsyncStream]):
        self._streams = deque(streams)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeAsyncStream:
        self.calls.append(kwargs)
        assert self._streams, "test fake received an unexpected async network call"
        return self._streams.popleft()


class FakeAsyncClient:
    def __init__(self, streams: Iterable[FakeAsyncStream]):
        self.responses = FakeAsyncResponses(streams)
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class BlockingCloseAsyncStream(FakeAsyncStream):
    def __init__(
        self,
        events: Iterable[Any],
        *,
        close_started: asyncio.Event,
        close_release: asyncio.Event,
        **kwargs: Any,
    ):
        super().__init__(events, **kwargs)
        self._close_started = close_started
        self._close_release = close_release

    async def aclose(self) -> None:
        self._close_started.set()
        await self._close_release.wait()
        self.closed = True


class FailingCloseAsyncStream(FakeAsyncStream):
    async def aclose(self) -> None:
        raise RuntimeError("stream close failed")


class BlockingFailingCloseAsyncStream(BlockingCloseAsyncStream):
    async def aclose(self) -> None:
        self._close_started.set()
        await self._close_release.wait()
        raise RuntimeError("stream close failed during cancellation")


def _async_adapter(
    async_client: FakeAsyncClient,
    *,
    limits: Any = None,
) -> tuple[CodexAsyncCompletionsAdapter, CodexCompletionsAdapter]:
    unused_sync_client = FakeSyncClient([])
    sync_adapter, _factory = _adapter(
        unused_sync_client,
        async_client_factory=CountingFactory(async_client),
        limits=limits,
    )
    return CodexAsyncCompletionsAdapter(sync_adapter), sync_adapter


@pytest.mark.asyncio
async def test_stateful_async_uses_native_async_stream_and_matches_sync_reducer():
    """Cancellation semantics require async iteration, not a worker-thread wrapper."""
    items = [_reasoning(), _message("parity")]
    sync_client = FakeSyncClient([FakeSyncStream(_events(items))])
    sync_adapter, _factory = _adapter(sync_client)
    sync_turn = _turn(sync_adapter, "same")

    async_client = FakeAsyncClient([FakeAsyncStream(_events(items))])
    async_adapter, _sync_holder = _async_adapter(async_client)
    with patch(
        "openviking.models.vlm.backends.codex_responses_adapter.asyncio.to_thread",
        side_effect=AssertionError("stateful async must not use asyncio.to_thread"),
    ):
        async_turn = await async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "same"}],
        )

    assert _canonical_items(async_turn.state) == _canonical_items(sync_turn.state)
    assert async_turn.state.generation == sync_turn.state.generation
    assert async_turn.result.choices[0].message.content == "parity"


@pytest.mark.asyncio
async def test_async_cancellation_closes_stream_propagates_and_publishes_nothing():
    """A cancelled task must not leak a half-built candidate state."""
    stream = FakeAsyncStream(
        [SimpleNamespace(type="response.output_item.done", item=_message("candidate"))],
        terminal_error=asyncio.CancelledError(),
    )
    async_client = FakeAsyncClient([stream])
    async_adapter, _sync_holder = _async_adapter(async_client)

    with pytest.raises(asyncio.CancelledError):
        await async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "cancel"}],
        )

    assert stream.closed is True
    assert async_client.close_calls == 1


@pytest.mark.asyncio
async def test_repeated_async_cancellation_waits_for_stream_and_client_cleanup():
    """A second cancel must not interrupt resource close or leak the chain slot."""
    iteration_started = asyncio.Event()
    iteration_release = asyncio.Event()
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    blocked_stream = BlockingCloseAsyncStream(
        _events([_message("never published")]),
        entered=iteration_started,
        release=iteration_release,
        close_started=close_started,
        close_release=close_release,
    )
    next_stream = FakeAsyncStream(_events([_message("after cleanup")]))
    async_client = FakeAsyncClient([blocked_stream, next_stream])
    async_adapter, _sync_holder = _async_adapter(
        async_client,
        limits=_limits(max_concurrent_chains=1),
    )
    task = asyncio.create_task(
        async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "cancel twice"}],
        )
    )
    await iteration_started.wait()

    task.cancel()
    await close_started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert task.done() is False
    close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert blocked_stream.closed is True
    assert async_client.close_calls == 1

    result = await async_adapter.create_with_state(
        state=None,
        expected_generation=None,
        model=MODEL,
        instructions=INSTRUCTIONS,
        messages=[{"role": "user", "content": "after cleanup"}],
    )
    assert result.result.choices[0].message.content == "after cleanup"


@pytest.mark.asyncio
async def test_async_stream_close_failure_still_closes_client():
    """One resource close failure must not skip cleanup of later resources."""
    stream = FailingCloseAsyncStream(_events([_message("response")]))
    async_client = FakeAsyncClient([stream])
    async_adapter, _sync_holder = _async_adapter(async_client)

    with pytest.raises(_error("CodexStateTransportError")):
        await async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "close failure"}],
        )

    assert async_client.close_calls == 1


@pytest.mark.asyncio
async def test_async_close_failure_does_not_mask_request_cancellation():
    """Cleanup errors must not replace the cancellation that initiated cleanup."""
    iteration_started = asyncio.Event()
    iteration_release = asyncio.Event()
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    stream = BlockingFailingCloseAsyncStream(
        _events([_message("never published")]),
        entered=iteration_started,
        release=iteration_release,
        close_started=close_started,
        close_release=close_release,
    )
    async_client = FakeAsyncClient([stream])
    async_adapter, _sync_holder = _async_adapter(async_client)
    task = asyncio.create_task(
        async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "cancel with close failure"}],
        )
    )
    await iteration_started.wait()

    task.cancel()
    await close_started.wait()
    close_release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert async_client.close_calls == 1


@pytest.mark.asyncio
async def test_concurrent_chain_limit_is_fail_fast_and_released_after_cancel():
    """The per-instance chain cap bounds memory without hidden queueing."""
    entered = asyncio.Event()
    release = asyncio.Event()
    blocked_stream = FakeAsyncStream(
        _events([_message("first")]),
        entered=entered,
        release=release,
    )
    second_stream = FakeAsyncStream(_events([_message("second")]))
    async_client = FakeAsyncClient([blocked_stream, second_stream])
    async_adapter, _sync_holder = _async_adapter(
        async_client,
        limits=_limits(max_concurrent_chains=1),
    )
    first_task = asyncio.create_task(
        async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "first"}],
        )
    )
    await entered.wait()

    with pytest.raises(_error("CodexStateConcurrencyError")):
        await async_adapter.create_with_state(
            state=None,
            expected_generation=None,
            model=MODEL,
            instructions=INSTRUCTIONS,
            messages=[{"role": "user", "content": "second"}],
        )

    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    assert blocked_stream.closed is True

    result = await async_adapter.create_with_state(
        state=None,
        expected_generation=None,
        model=MODEL,
        instructions=INSTRUCTIONS,
        messages=[{"role": "user", "content": "after release"}],
    )
    assert result.result.choices[0].message.content == "second"


def test_state_adapter_initialization_is_singleton_under_thread_race():
    """Competing first turns must not publish states signed by a discarded adapter."""
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "api_key": "explicit-test-key",
            "responses_state_enabled": True,
        }
    )
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def slow_credentials():
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return (
            "explicit-test-key",
            APPROVED_ORIGIN,
            "explicit:principal",
            "explicit:credential",
        )

    with patch.object(
        vlm,
        "_resolve_state_runtime_credentials",
        side_effect=slow_credentials,
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(vlm._get_or_create_state_adapters)
            assert entered.wait(timeout=2)
            second = pool.submit(vlm._get_or_create_state_adapters)
            release.set()
            first_result = first.result(timeout=2)
            second_result = second.result(timeout=2)

    assert first_result[0] is second_result[0]
    assert first_result[1] is second_result[1]
    assert calls == 1


@pytest.mark.asyncio
async def test_vlm_async_state_credential_io_stays_off_event_loop():
    """OAuth filesystem/refresh work must not block native async streaming."""
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "responses_state_enabled": True,
        }
    )
    loop_thread = threading.get_ident()
    sync_threads: list[int] = []
    credentials = {
        "api_key": (
            "eyJhbGciOiJub25lIn0.eyJpc3MiOiJodHRwczovL2F1dGgub3BlbmFpLmNvbSIsInN1YiI6InVzZXItMSJ9."
        ),
        "base_url": APPROVED_ORIGIN,
        "auth_owner": "openviking",
        "source": "openviking",
        "path": "/redacted/auth-slot",
        "client_id": "codex-client",
    }

    def sync_resolver():
        sync_threads.append(threading.get_ident())
        return credentials

    async_resolver = AsyncMock(return_value=credentials)
    async_client = FakeAsyncClient([FakeAsyncStream(_events([_message("async result")]))])
    with (
        patch(
            "openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials",
            side_effect=sync_resolver,
        ),
        patch(
            "openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials_async",
            async_resolver,
        ),
        patch.object(
            vlm,
            "_build_async_state_responses_client",
            return_value=async_client,
        ),
    ):
        turn = await vlm.get_completion_with_state_async("initial")

    assert turn.result == "async result"
    assert sync_threads and all(thread_id != loop_thread for thread_id in sync_threads)
    async_resolver.assert_awaited_once()


def test_state_tool_projection_never_traces_visible_or_opaque_content():
    """The state-specific visible projection must not inherit content logging."""
    secret = "SECURITY_SENTINEL_VISIBLE_TOOL_CONTENT"
    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": MODEL,
            "api_key": "explicit-test-key",
            "responses_state_enabled": True,
        }
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=secret,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="lookup",
                                arguments='{"query":"value"}',
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            opaque_provider_metadata=secret,
        ),
    )
    turn_type = _symbol("CodexResponsesTurn")
    turn = turn_type(result=response, state=object())

    with patch("openviking.models.vlm.backends.openai_vlm.tracer.info") as trace_info:
        projected = vlm._project_state_turn(turn, has_tools=True)

    assert projected.result.content == secret
    assert all(secret not in str(call) for call in trace_info.call_args_list)


def test_state_repr_and_logs_redact_opaque_items_integrity_and_credentials(caplog):
    """Opaque state is memory-only and must not escape through routine diagnostics."""
    client = FakeSyncClient([FakeSyncStream(_events([_message("visible", secret=OPAQUE_SECRET)]))])
    adapter, _factory = _adapter(
        client,
        credential=f"credential:{OPAQUE_SECRET}",
        state_key=OPAQUE_SECRET.encode("utf-8").ljust(32, b"x"),
    )

    with caplog.at_level(logging.DEBUG):
        turn = _turn(adapter, "initial")

    rendered = repr(turn.state)
    logs = caplog.text
    assert OPAQUE_SECRET not in rendered
    assert OPAQUE_SECRET not in logs
    assert "integrity_tag" not in rendered
    assert "response_items" not in rendered


def test_binding_errors_do_not_include_secret_values():
    """Fail-loud validation still must fail closed on credential disclosure."""
    _adapter_instance, state, _client, _factory = _initial_state([_message("initial")])
    secret_credential = f"credential:{OPAQUE_SECRET}"
    target_client = FakeSyncClient([])
    target_adapter, _target_factory = _adapter(
        target_client,
        credential=secret_credential,
    )

    with pytest.raises(_error("CodexStateBindingError")) as exc_info:
        _turn(
            target_adapter,
            "next",
            state=state,
            expected_generation=state.generation,
        )

    assert OPAQUE_SECRET not in str(exc_info.value)
    assert secret_credential not in str(exc_info.value)


def test_legacy_create_remains_stateless_and_uses_no_state_metadata():
    """The opt-in feature must not alter existing create() request semantics."""
    client = FakeSyncClient([FakeSyncStream(_events([_message("legacy")]))])
    adapter = CodexCompletionsAdapter(lambda: client, MODEL)

    response = adapter.create(
        model=MODEL,
        messages=[{"role": "user", "content": "legacy"}],
    )

    assert response.choices[0].message.content == "legacy"
    request = client.responses.calls[0]
    assert request["store"] is False
    assert "context_management" not in request
    assert "conversation" not in request
    assert "previous_response_id" not in request
    assert not any(key.startswith("state_") for key in request)
