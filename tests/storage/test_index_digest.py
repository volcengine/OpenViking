# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.index_digest import (
    canonical_digest,
    embedding_input_digest,
    normalize_source_text,
    source_digest,
)


def test_source_digest_normalizes_only_line_endings() -> None:
    assert normalize_source_text("a\r\nb\rc") == "a\nb\nc"
    assert source_digest("a\r\nb\rc") == source_digest("a\nb\nc")
    assert source_digest(" text ") != source_digest("text")
    assert source_digest("é") != source_digest("e\u0301")


def test_source_digest_has_stable_test_vector() -> None:
    assert source_digest("") == (
        "sha256:v1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_canonical_digest_ignores_mapping_order() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


def test_embedding_input_digest_covers_multimodal_payload() -> None:
    first = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "x"}},
    ]
    second = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "y"}},
    ]
    assert embedding_input_digest(first) != embedding_input_digest(second)
