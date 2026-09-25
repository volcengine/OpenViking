# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Headings inside fenced code blocks must not split a Markdown document."""

import pytest

from openviking.parse.parsers.markdown import MarkdownParser


def _titles(content: str) -> list[str]:
    return [title for _, _, title, _ in MarkdownParser()._find_headings(content)]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("# Setup\n~~~bash\n# install deps\n~~~\n## Run\n", id="tilde-fence"),
        pytest.param("# Setup\n```c++\n# define X 1\n```\n## Run\n", id="info-string-with-symbols"),
        pytest.param(
            '# Setup\n```py title="a.py"\n# comment\n```\n## Run\n',
            id="info-string-with-attributes",
        ),
        pytest.param(
            "# Setup\r\n```bash\r\n# install deps\r\n```\r\n## Run\r\n", id="crlf-line-endings"
        ),
        pytest.param(
            "# Setup\n````md\n```\n# example heading\n```\n````\n## Run\n",
            id="longer-fence-around-example",
        ),
        pytest.param("# Setup\n```bash\n# install deps\n```\n## Run\n", id="plain-fence"),
    ],
)
def test_comment_lines_in_fenced_code_are_not_headings(content: str) -> None:
    assert _titles(content) == ["Setup", "Run"]


def test_heading_between_code_blocks_is_kept() -> None:
    # The old regex paired the first block's closing fence with the second block's
    # opener, hiding the real heading between them.
    content = "# Build\n```c++\nint x;\n```\n## Next\ntext\n```py\nx = 1\n```\n"

    assert _titles(content) == ["Build", "Next"]


def test_unclosed_fence_runs_to_end_of_document() -> None:
    assert _titles("# Setup\n```bash\n# install deps\n") == ["Setup"]


def test_backtick_line_with_backticks_in_info_string_is_not_a_fence() -> None:
    assert _titles("# Setup\n```inline``` text\n## Run\n") == ["Setup", "Run"]
