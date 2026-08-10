# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.memory_file_utils import memory_type_from_uri


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        (
            "viking://user/memories/memories/preferences/topic.md",
            "preferences",
        ),
        (
            "viking://user/alice/peers/support/memories/experiences/case.md",
            "experiences",
        ),
        ("viking://user/memories/preferences/topic.md", "preferences"),
        ("viking://user/alice/memories/profile.md", "profile"),
        (
            "viking://user/alice/memories/entities/memories/topic.md",
            "entities",
        ),
        (
            "viking://user/peers/support/memories/preferences/topic.md",
            "preferences",
        ),
        ("viking://agent/code-agent/memories/facts/project.md", "facts"),
        ("viking://user/alice/resources/memories/topic.md", None),
    ],
)
def test_memory_type_from_uri_uses_namespace_grammar(uri, expected):
    assert memory_type_from_uri(uri) == expected
    assert MemoryUpdater.memory_type_from_uri(uri) == expected
