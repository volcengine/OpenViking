import pytest

from openviking_gateway.protocols import usage_of
from openviking_gateway.tool_protocols import ResponseCapture
from openviking_gateway.tool_protocols.common import add_usage


@pytest.mark.parametrize(
    ("usage", "anthropic", "expected"),
    [
        # Each protocol's own form.
        (
            {
                "input_tokens": 100,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 50,
            },
            True,
            (950, 800, 50),
        ),
        (
            {"prompt_tokens": 950, "prompt_tokens_details": {"cached_tokens": 800}},
            False,
            (950, 800, 0),
        ),
        (
            {"input_tokens": 950, "input_tokens_details": {"cached_tokens": 800}},
            False,
            (950, 800, 0),
        ),
        # A zero-filled Anthropic field no longer hides the count a relay put in details.
        (
            {
                "prompt_tokens": 950,
                "prompt_tokens_details": {"cached_tokens": 800},
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            False,
            (950, 800, 0),
        ),
        # LiteLLM: Chat totals already hold both cache fields it adds at the top.
        (
            {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {"cached_tokens": 800},
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 50,
            },
            False,
            (1000, 800, 50),
        ),
        ({"input_tokens": 950, "cache_read_input_tokens": 800}, False, (950, 800, 0)),
        # Anthropic-compatible endpoints that also send a Chat total.
        (
            {"prompt_tokens": 0, "input_tokens": 100, "cache_read_input_tokens": 800},
            True,
            (900, 800, 0),
        ),
        (
            {"input_tokens": 100, "cache_read_input_tokens": None, "cached_tokens": 800},
            True,
            (900, 800, 0),
        ),
        # DeepSeek, Kimi, DashScope and OpenRouter names.
        (
            {"prompt_tokens": 950, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 150},
            False,
            (950, 800, 0),
        ),
        (
            {
                "prompt_tokens": 19,
                "cached_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 10},
            },
            False,
            (19, 10, 0),
        ),
        (
            {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {"cached_tokens": 600, "cache_creation_input_tokens": 300},
            },
            False,
            (1000, 600, 300),
        ),
        (
            {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 300},
            },
            False,
            (1000, 600, 300),
        ),
        # new-api fills both totals, one with zero.
        (
            {
                "prompt_tokens": 0,
                "input_tokens": 950,
                "input_tokens_details": {"cached_tokens": 800},
            },
            False,
            (950, 800, 0),
        ),
        (
            {
                "prompt_tokens": 950,
                "input_tokens": 0,
                "prompt_tokens_details": {"cached_tokens": 800},
                "input_tokens_details": None,
            },
            False,
            (950, 800, 0),
        ),
        # Older new-api: a Chat total without the cache, which is added back.
        (
            {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 800}},
            False,
            (810, 800, 0),
        ),
        # No cache field at all.
        ({"input_tokens": 100, "cache_read_input_tokens": None}, True, (100, 0, 0)),
        ({"prompt_tokens": 950, "prompt_tokens_details": {}}, False, (950, 0, 0)),
    ],
)
def test_usage_of_reads_every_upstream_form(usage, anthropic, expected):
    result = usage_of({"usage": usage}, anthropic)
    assert (
        result["input_tokens"],
        result["cached_tokens"],
        result["cache_write_tokens"],
    ) == expected


def test_add_usage_skips_nulls_and_keeps_flags():
    total = {}
    for usage in (
        {"cache_read_input_tokens": None, "prompt_tokens_details": None, "is_byok": False},
        {"cache_read_input_tokens": 5, "prompt_tokens_details": {"cached_tokens": 80}},
        {"cache_read_input_tokens": None, "prompt_tokens_details": None, "is_byok": False},
    ):
        add_usage(total, usage)
    assert total == {
        "cache_read_input_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 80},
        "is_byok": False,
    }


@pytest.mark.parametrize("late", [None, 0])
def test_anthropic_delta_keeps_start_cache(late):
    capture = ResponseCapture("anthropic")
    start = {"input_tokens": 12, "cache_read_input_tokens": 88}
    capture.event({"type": "message_start", "message": {"id": "x", "usage": start}})
    capture.event(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 20, "input_tokens": late, "cache_read_input_tokens": late},
        }
    )
    assert capture.usage["input_tokens"] == 100
    assert capture.usage["cached_tokens"] == 88


def test_anthropic_delta_carries_relayed_cache():
    """Relays converting other protocols send zeros at the start and counts at the end."""
    capture = ResponseCapture("anthropic")
    start = {"input_tokens": 12, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    capture.event({"type": "message_start", "message": {"id": "x", "usage": start}})
    capture.event(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 20, "input_tokens": 12, "cache_read_input_tokens": 88},
        }
    )
    assert capture.usage["input_tokens"] == 100
    assert capture.usage["cached_tokens"] == 88


def test_chat_stream_null_details_keep_cache():
    capture = ResponseCapture("chat")
    capture.event(
        {
            "id": "x",
            "choices": [],
            "usage": {"prompt_tokens": 950, "prompt_tokens_details": {"cached_tokens": 800}},
        }
    )
    capture.event(
        {
            "id": "x",
            "choices": [],
            "usage": {"prompt_tokens": 950, "completion_tokens": 20, "prompt_tokens_details": None},
        }
    )
    assert capture.usage["cached_tokens"] == 800
