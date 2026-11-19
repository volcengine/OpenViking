# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import yaml

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.system_assets import builtin_memory_templates_dir


def _tools_content_template() -> str:
    template_path = builtin_memory_templates_dir() / "tools.yaml"
    return yaml.safe_load(template_path.read_text(encoding="utf-8"))["content_template"]


def test_tools_template_treats_none_counts_as_zero():
    rendered = MemoryFileUtils.write(
        MemoryFile(
            extra_fields={
                "tool_name": "read_file",
                "static_desc": "Reads files",
                "call_count": None,
                "success_time": None,
            }
        ),
        content_template=_tools_content_template(),
    )

    assert "- Success rate: 0% (0/0)" in rendered


def test_tools_template_keeps_success_rate_for_positive_counts():
    rendered = MemoryFileUtils.write(
        MemoryFile(
            extra_fields={
                "tool_name": "read_file",
                "static_desc": "Reads files",
                "call_count": 4,
                "success_time": 3,
            }
        ),
        content_template=_tools_content_template(),
    )

    assert "- Success rate: 75% (3/4)" in rendered
