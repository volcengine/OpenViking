# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.utils.content_visibility import visible_content

MEMORY_URI = "viking://user/default/memories/notes/private.md"


@pytest.mark.parametrize(
    "raw_content",
    [
        'visible\n\n<!-- MEMORY_FIELDS\n{"secret":"hidden"}\n-->',
        'visible\n<!-- MEMORY_FIELDS\n{"secret":"hidden"}\n-->',
        'visible\n\n<!-- MEMORY_FIELDS {"secret":"hidden"} -->',
        'visible <!-- MEMORY_FIELDS {"secret":"hidden"} -->',
        '<!-- MEMORY_FIELDS {"secret":"hidden"} -->',
    ],
)
def test_visible_content_strips_supported_memory_fields_trailers(raw_content):
    expected = "" if raw_content.startswith("<!--") else "visible"

    assert visible_content(raw_content, uri=MEMORY_URI) == expected


@pytest.mark.parametrize(
    "raw_content",
    [
        "visible <!-- ordinary comment -->",
        "visible <!-- MEMORY_FIELDS_EXAMPLE {} -->",
        'visible <!-- MEMORY_FIELDS {"example":true} -->\nmore',
    ],
)
def test_visible_content_preserves_non_reserved_or_non_trailing_comments(raw_content):
    assert visible_content(raw_content, uri=MEMORY_URI) == raw_content


def test_visible_content_preserves_reserved_comment_outside_memory_namespace():
    raw_content = 'visible <!-- MEMORY_FIELDS {"example":true} -->'

    assert visible_content(raw_content, uri="viking://resources/example.md") == raw_content


def test_visible_content_strips_repeated_memory_fields_trailers():
    raw_content = """visible
<!-- MEMORY_FIELDS {"secret":"old"} -->
<!-- MEMORY_FIELDS
{"secret":"new"}
-->"""

    assert visible_content(raw_content, uri=MEMORY_URI) == "visible"
