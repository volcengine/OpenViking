from types import SimpleNamespace

import pytest

import vikingbot.providers.base as provider_base
from vikingbot.providers.base import (
    build_stream_response,
    merge_stream_tool_call_delta,
    parse_tool_arguments,
)


def _tool_call(*, index=None, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_tool_arguments_repair_malformed_json_object():
    raw = '{"pages": [], "files": [], "links": [],}'

    assert parse_tool_arguments(raw) == {"pages": [], "files": [], "links": []}


def test_tool_arguments_preserve_unrepairable_non_object():
    raw = "not-json"

    assert parse_tool_arguments(raw) == {"raw": raw}


def test_tool_arguments_do_not_repair_truncated_json_object(monkeypatch):
    def fail_if_called(_raw):
        raise AssertionError("truncated JSON must not be repaired")

    monkeypatch.setattr(provider_base.json_repair, "loads", fail_if_called)
    raw = '{"pages": [], "files": [{"path": "logic/related_work.md", "content": "'

    assert parse_tool_arguments(raw) == {"raw": raw}


def test_tool_arguments_fall_back_when_repair_rejects_input(monkeypatch):
    def reject_input(_raw):
        raise ValueError("invalid input")

    monkeypatch.setattr(provider_base.json_repair, "loads", reject_input)

    assert parse_tool_arguments("{invalid}") == {"raw": "{invalid}"}


def test_tool_arguments_do_not_hide_unexpected_repair_errors(monkeypatch):
    def fail_unexpectedly(_raw):
        raise RuntimeError("repair implementation failed")

    monkeypatch.setattr(provider_base.json_repair, "loads", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="repair implementation failed"):
        parse_tool_arguments("{invalid}")


def test_missing_stream_tool_call_index_uses_chunk_local_order():
    raw_tool_calls = {}
    chunks = [
        [
            _tool_call(call_id="call_search", name="search", arguments='{"query"'),
            _tool_call(call_id="call_read", name="read_file", arguments='{"path"'),
        ],
        [
            _tool_call(arguments=':"openviking"}'),
            _tool_call(arguments=':"bot.py"}'),
        ],
    ]

    for chunk in chunks:
        for fallback_index, delta_tool_call in enumerate(chunk):
            merge_stream_tool_call_delta(
                raw_tool_calls,
                delta_tool_call,
                fallback_index=fallback_index,
            )

    response = build_stream_response(
        content="",
        reasoning_content="",
        raw_tool_calls=raw_tool_calls,
        finish_reason="tool_calls",
    )

    assert [(tc.id, tc.name, tc.arguments) for tc in response.tool_calls] == [
        ("call_search", "search", {"query": "openviking"}),
        ("call_read", "read_file", {"path": "bot.py"}),
    ]


def test_explicit_stream_tool_call_index_wins_over_chunk_local_order():
    raw_tool_calls = {}

    for fallback_index, delta_tool_call in enumerate(
        [
            _tool_call(index=1, call_id="call_second", name="second", arguments="{}"),
            _tool_call(index=0, call_id="call_first", name="first", arguments="{}"),
        ]
    ):
        merge_stream_tool_call_delta(
            raw_tool_calls,
            delta_tool_call,
            fallback_index=fallback_index,
        )

    response = build_stream_response(
        content="",
        reasoning_content="",
        raw_tool_calls=raw_tool_calls,
        finish_reason="tool_calls",
    )

    assert [(tc.id, tc.name) for tc in response.tool_calls] == [
        ("call_first", "first"),
        ("call_second", "second"),
    ]
