# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

from openviking.session.memory.tools import (
    add_tool_call_pair_to_messages,
    optimize_tool_result,
)


def test_optimize_tool_result_truncates_read_content():
    long_content = "\n".join(f"{i}\t" for i in range(1, 900))
    result = {"uri": "viking://user/u/memories/profile.md", "content": long_content}
    optimized = optimize_tool_result("read", result)
    assert isinstance(optimized, dict)
    assert "content" in optimized
    assert len(optimized["content"]) < len(long_content)
    assert "truncated" in optimized["content"]
    # Original must stay untouched
    assert len(result["content"]) == len(long_content)


def test_add_tool_call_pair_uses_optimized_read_result():
    long_content = "\n".join(f"{i}\t" for i in range(1, 900))
    original = {"uri": "viking://user/u/memories/profile.md", "content": long_content}
    messages = []
    add_tool_call_pair_to_messages(
        messages,
        call_id="call_1",
        tool_name="read",
        params={"uri": original["uri"]},
        result=original,
    )
    assert len(messages) == 1
    payload = json.loads(messages[0]["content"])
    assert payload["tool_call_name"] == "read"
    assert len(payload["result"]["content"]) < len(long_content)
    assert "truncated" in payload["result"]["content"]
    # Caller-held original remains full for apply/write paths
    assert len(original["content"]) == len(long_content)

def test_optimize_tool_result_summarizes_error_with_room_for_diagnostics():
    long_err = "patch failed: " + ("x" * 400)
    optimized = optimize_tool_result("read", {"error": long_err})
    assert optimized == {"error": long_err[:250]}
    assert len(optimized["error"]) == 250


def test_optimize_tool_result_preserves_known_error_labels():
    optimized = optimize_tool_result("search", {"error": "File not found at viking://x"})
    assert optimized == {"error": "File not found"}


def test_optimize_tool_result_compacts_search_memories():
    result = {
        "memories": [
            {"uri": "viking://a/profile.md", "score": 0.9, "extra": "drop"},
            {"uri": "viking://a/note.abstract.md", "score": 0.8},
            {"uri": "viking://a/keep.md", "score": 0.7},
        ]
    }
    optimized = optimize_tool_result("search", result)
    assert optimized == [
        {"uri": "viking://a/profile.md", "score": 0.9},
        {"uri": "viking://a/keep.md", "score": 0.7},
    ]


def test_add_tool_call_pair_uses_optimized_error_result():
    messages = []
    add_tool_call_pair_to_messages(
        messages,
        call_id="call_err",
        tool_name="read",
        params={"uri": "viking://x"},
        result={"error": "Timeout while reading"},
    )
    payload = json.loads(messages[0]["content"])
    assert payload["result"] == {"error": "Timeout"}
