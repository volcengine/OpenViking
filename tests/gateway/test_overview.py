"""Overview aggregates and prompt-cache alerts read from request logs."""

import pytest

from openviking_gateway.overview import overview_summary

NOW = 1_000_000.0


def call(minutes_ago, **fields):
    """A long, missed first call that finished `minutes_ago` before NOW."""
    return {
        "kind": "user",
        "session": "s1",
        "protocol": "anthropic",
        "credential_id": "k1",
        "upstream_id": "chat",
        "model": "m",
        "status": 200,
        "time": NOW - minutes_ago * 60,
        "duration_ms": 2000,
        "input_tokens": 40_000,
        "cached_tokens": 0,
        "cache_write_tokens": 40_000,
        **fields,
    }


def alerts(*logs, hit=True):
    """Overview cache alerts; `hit` adds another conversation that hit the upstream."""
    logs = sorted(logs, key=lambda log: -log["time"])
    if hit:
        logs.append(call(600, session="warm", cached_tokens=30_000))
    return overview_summary(logs, NOW)["cache_alerts"]


def streak(*overrides):
    """Calls a minute apart, newest first, each updated with its override."""
    return [call(i, **override) for i, override in enumerate(overrides)]


def test_cache_alert_after_consecutive_long_misses():
    result = alerts(*streak({}, {"input_tokens": 39_000}, {"input_tokens": 38_000}))
    assert result == {
        "misses": {
            "conversations": 1,
            "streak": 3,
            "input_tokens": 38_000,
            "no_writes": False,
            "recent": [{"upstream_id": "chat", "model": "m", "streak": 3, "last_at": NOW}],
        },
        "unreported": [],
    }
    assert alerts(*streak({}, {})) is None
    assert overview_summary([], NOW)["cache_alerts"] is None


def test_cache_alert_summary_speaks_for_every_conversation():
    logs = [call(i * 0.1, session=f"s{i}", input_tokens=40_000 + i) for i in range(25)]
    logs = [{**log, "time": log["time"] - j * 60} for log in logs for j in range(3)]
    logs[-1]["input_tokens"] = 33_000
    misses = alerts(*logs)["misses"]
    assert [miss["last_at"] for miss in misses["recent"]] == [NOW, NOW - 6, NOW - 12]
    assert (misses["conversations"], misses["streak"], misses["input_tokens"]) == (25, 3, 33_000)


def test_cache_alert_suggests_cache_control_only_without_any_write():
    silent = {"cache_write_tokens": 0}
    assert alerts(*streak(silent, silent, silent))["misses"]["no_writes"] is True
    assert alerts(*streak(silent, silent, {}))["misses"]["no_writes"] is False
    chat = {**silent, "protocol": "chat"}
    assert alerts(*streak(chat, chat, chat))["misses"]["no_writes"] is False


@pytest.mark.parametrize(
    "older",
    [
        {"cached_tokens": 30_000},
        {"input_tokens": 20_000},
        {"window_reset": True},
        {"compaction_tokens": 9000},
        {"degradation": "upstream_changed"},
        {"degradation": "ark_cache_parameters_changed"},
        {"model": "other"},
        {"upstream_id": "other"},
        {"tools_tokens": 500},
        {"tool_skip_reason": "tool_name_collision"},
        {"input_tokens": 60_000},  # Larger than the newer call: a /clear or a branch since.
    ],
)
def test_cache_alert_breaks(older):
    assert alerts(*streak({}, {}, {}, older))["misses"]["streak"] == 3
    assert alerts(*streak({}, older, {}, {})) is None


def test_cache_alert_newest_call_decides():
    assert alerts(*streak({"cached_tokens": 30_000}, {}, {}, {})) is None
    assert alerts(*streak({"input_tokens": 20_000}, {}, {}, {})) is None


def test_cache_alert_expired_cache_breaks():
    assert alerts(call(0), call(1), call(2), call(8))["misses"]["streak"] == 3
    assert alerts(call(0), call(6), call(7), call(8)) is None


def test_cache_alert_ignores_other_requests_and_concurrent_calls():
    logs = streak({}, {}, {})
    logs += [
        call(0.5, kind="subagent", model="small", input_tokens=1000),
        call(1.5, kind="auxiliary", input_tokens=500, cached_tokens=400),
        call(1.2, status=500),
        call(0.2, session=None),
        call(1.1, duration_ms=60_000, cached_tokens=30_000),  # Overlaps the newer call.
    ]
    assert alerts(*logs)["misses"]["streak"] == 3


def test_cache_alert_ignores_stale_conversations():
    assert alerts(*[call(1500 + i) for i in range(3)]) is None


def test_cache_alert_prefers_first_upstream_usage():
    first = {"first_upstream_input_tokens": 40_000, "first_upstream_cached_tokens": 0}
    first |= {"first_upstream_cache_write_tokens": 0}
    hidden = {"input_tokens": 90_000, "cached_tokens": 80_000}
    misses = alerts(*streak(*[{**hidden, **first}] * 3))["misses"]
    assert misses["input_tokens"] == 40_000 and misses["no_writes"] is True
    hit = {**first, "first_upstream_cached_tokens": 30_000}
    assert alerts(*streak(hit, *[{**hidden, **first}] * 2)) is None


def test_cache_alert_upstreams_without_hits_are_unreported():
    other = {"session": "s2", "upstream_id": "silent"}
    result = alerts(*streak({}, {}, {}), *streak(other, other, other), hit=False)
    assert result == {"misses": None, "unreported": ["chat", "silent"]}
    # Only recent hits count: a relay that started dropping cache fields shows up today.
    old_hit = call(3 * 24 * 60, session="old", cached_tokens=30_000)
    assert alerts(*streak({}, {}, {}), old_hit, hit=False)["unreported"] == ["chat"]
