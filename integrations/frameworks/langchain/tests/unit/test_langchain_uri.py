from __future__ import annotations

import pytest

from langchain_openviking._uri import classify_uri


@pytest.mark.parametrize(
    ("uri", "expected_parts", "expected_scope", "expected_content_index"),
    [
        (
            "viking://~/memories/store/data/users/ada.json",
            ("~", "memories", "store", "data", "users", "ada.json"),
            "user",
            1,
        ),
        # Legacy uid-less roots may still sit in older store configs; keep classifying them.
        (
            "viking://user/memories/store/data/users/ada.json",
            ("user", "memories", "store", "data", "users", "ada.json"),
            "user",
            1,
        ),
        (
            "viking://user/default/memories/store/index/users/ada.md",
            ("user", "default", "memories", "store", "index", "users", "ada.md"),
            "user",
            2,
        ),
        (
            "viking://user/default/peers/assistant/resources/store/data.json",
            ("user", "default", "peers", "assistant", "resources", "store", "data.json"),
            "user",
            4,
        ),
        (
            "viking://~/peers/assistant/memories/store/index.md",
            ("~", "peers", "assistant", "memories", "store", "index.md"),
            "user",
            3,
        ),
        (
            "viking://user/peers/assistant/memories/store/index.md",
            ("user", "peers", "assistant", "memories", "store", "index.md"),
            "user",
            3,
        ),
        (
            "viking://agent/default/skills/review/SKILL.md",
            ("agent", "default", "skills", "review", "SKILL.md"),
            "agent",
            2,
        ),
        (
            "viking://resources/shared.md",
            ("resources", "shared.md"),
            "resources",
            None,
        ),
        (
            "viking://user/default/sessions/thread-1",
            ("user", "default", "sessions", "thread-1"),
            "user",
            None,
        ),
        (
            "/user/default/memories/store/data.json?profile=1",
            ("user", "default", "memories", "store", "data.json"),
            "user",
            2,
        ),
    ],
)
def test_private_uri_classification_preserves_server_namespace_shapes(
    uri,
    expected_parts,
    expected_scope,
    expected_content_index,
):
    classification = classify_uri(uri)

    assert classification.parts == expected_parts
    assert classification.scope == expected_scope
    assert classification.content_index == expected_content_index
