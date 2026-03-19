# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from openviking.core.context import ResourceContentType
from openviking.utils.embedding_utils import (
    _should_prefer_summary_for_vectorization,
    _truncate_text_for_embedding,
    get_resource_content_type,
)


def test_get_resource_content_type_supports_common_text_build_files():
    assert get_resource_content_type("App.css") == ResourceContentType.TEXT
    assert get_resource_content_type("utils.cmake") == ResourceContentType.TEXT
    assert get_resource_content_type("go.mod") == ResourceContentType.TEXT
    assert get_resource_content_type("uv.lock") == ResourceContentType.TEXT
    assert get_resource_content_type("Makefile") == ResourceContentType.TEXT
    assert get_resource_content_type("LICENSE") == ResourceContentType.TEXT
    assert get_resource_content_type("MANIFEST.in") == ResourceContentType.TEXT
    assert get_resource_content_type("ov.conf.example") == ResourceContentType.TEXT


def test_generated_lockfiles_prefer_summary_for_vectorization():
    assert _should_prefer_summary_for_vectorization("Cargo.lock") is True
    assert _should_prefer_summary_for_vectorization("uv.lock") is True
    assert _should_prefer_summary_for_vectorization("main.py") is False


def test_truncate_text_for_embedding_caps_raw_content_length():
    text = "x" * 20000

    truncated = _truncate_text_for_embedding(text, max_chars=100)

    assert len(truncated) < len(text)
    assert truncated.endswith("...(truncated)")
