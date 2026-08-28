# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.utils.line_numbers import (
    add_line_numbers,
    strip_line_numbers,
)


def test_strip_line_numbers_removes_repeated_prefixes():
    content = "## Title\n- fact one"
    numbered_twice = add_line_numbers(add_line_numbers(content))

    assert strip_line_numbers(numbered_twice) == content
    assert strip_line_numbers(numbered_twice) == strip_line_numbers(numbered_twice)
