# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""The consistency checker must expect records only for indexable namespaces."""

import pytest

from openviking.storage.index_consistency import _is_index_scope


@pytest.mark.parametrize(
    "uri",
    [
        "viking://session/abc/turn",
        "viking://system",
        "viking://system/compile",
        "viking://system/compile/example-skills/demo",
        "viking://system/memory/templates",
        "viking://agent",
    ],
)
def test_non_indexable_scopes_are_skipped(uri: str):
    """FILE_ONLY namespaces and sessions cannot have vector records created for them."""
    assert _is_index_scope(uri) is False


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/project/doc.md",
        "viking://user/alice/skills",
        "viking://agent/skills/demo",
    ],
)
def test_indexable_scopes_are_kept(uri: str):
    assert _is_index_scope(uri) is True


def test_malformed_uri_is_not_an_index_scope():
    assert _is_index_scope("not a uri") is False
