# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for serializing memory tool results that carry datetime metadata (#6975)."""

import json

import pytest

from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils


def test_datetime_metadata_is_serialized_as_iso_string():
    """A read result with created_at/updated_at must not break the eager-prefetch message."""
    raw = 'Example\n\n<!-- MEMORY_FIELDS\n{"created_at":"2026-11-03T12:34:56+08:00"}\n-->'
    result = MemoryFileUtils.read(raw, uri="viking://test.md").to_metadata()

    messages = []
    add_tool_call_pair_to_messages(messages, 1, "read", {"uri": "viking://test.md"}, result)

    payload = json.loads(messages[0]["content"])
    assert payload["result"]["created_at"] == "2026-11-03T12:34:56+08:00"


def test_unsupported_objects_still_raise_type_error():
    """The datetime serializer must not become a catch-all for arbitrary objects."""
    messages = []
    with pytest.raises(TypeError):
        add_tool_call_pair_to_messages(messages, 1, "read", {}, {"bad": object()})
