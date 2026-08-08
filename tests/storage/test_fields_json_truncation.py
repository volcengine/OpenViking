# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LocalCollection._truncate_fields_json.

Verifies that the ``fields`` JSON blob written to the local vectordb
``bytes_row`` store never exceeds the uint16 string-length limit (65 535
bytes), even when scalar metadata (abstract, description, …) is large.

Related: #2967 (content field), #2774 (abstract cap), #2117 / PR #2171.
"""

import json

import pytest

from openviking.storage.vectordb.collection.local_collection import LocalCollection
from openviking.storage.vectordb.store.bytes_row import STRING_MAX_UINT16_LENGTH


class TestTruncateFieldsJson:
    """Unit tests for the static truncation helper."""

    def test_small_payload_unchanged(self):
        data = {"uri": "viking://x", "abstract": "short", "level": 2}
        blob = LocalCollection._truncate_fields_json(data)
        assert json.loads(blob) == data

    def test_large_abstract_truncated(self):
        data = {
            "uri": "viking://user/memories/events/big.md",
            "abstract": "A" * 70_000,
            "level": 2,
        }
        blob = LocalCollection._truncate_fields_json(data)
        assert len(blob.encode("utf-8")) <= STRING_MAX_UINT16_LENGTH
        parsed = json.loads(blob)
        assert parsed["uri"] == data["uri"]
        assert parsed["level"] == 2
        assert "truncated" in parsed["abstract"]

    def test_multiple_large_fields(self):
        data = {
            "uri": "viking://res/big",
            "abstract": "B" * 40_000,
            "description": "C" * 40_000,
            "name": "D" * 10_000,
        }
        blob = LocalCollection._truncate_fields_json(data)
        assert len(blob.encode("utf-8")) <= STRING_MAX_UINT16_LENGTH
        parsed = json.loads(blob)
        # At least the URI must survive untouched.
        assert parsed["uri"] == data["uri"]

    def test_non_string_fields_preserved(self):
        data = {
            "uri": "viking://x",
            "abstract": "E" * 70_000,
            "level": 1,
            "active_count": 42,
            "created_at": "2026-01-01T00:00:00Z",
        }
        blob = LocalCollection._truncate_fields_json(data)
        parsed = json.loads(blob)
        assert parsed["level"] == 1
        assert parsed["active_count"] == 42
        assert parsed["created_at"] == data["created_at"]

    def test_empty_dict(self):
        blob = LocalCollection._truncate_fields_json({})
        assert json.loads(blob) == {}

    def test_boundary_exact_fit(self):
        # Build a payload that is exactly at the limit — should pass through.
        base = {"uri": "viking://x", "abstract": ""}
        base_blob = json.dumps(base, ensure_ascii=False)
        overhead = len(base_blob.encode("utf-8"))
        fill = STRING_MAX_UINT16_LENGTH - overhead - 2  # quotes around value
        data = {"uri": "viking://x", "abstract": "F" * fill}
        blob = LocalCollection._truncate_fields_json(data)
        assert len(blob.encode("utf-8")) <= STRING_MAX_UINT16_LENGTH

    def test_unicode_multibyte_truncation(self):
        # Multi-byte UTF-8 chars (emoji = 4 bytes each).
        data = {"uri": "viking://x", "abstract": "🔥" * 20_000}
        blob = LocalCollection._truncate_fields_json(data)
        assert len(blob.encode("utf-8")) <= STRING_MAX_UINT16_LENGTH
        # Must be valid JSON (no broken surrogates).
        json.loads(blob)
