# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

import pytest
import yaml
from jinja2 import Template


@pytest.mark.parametrize(
    "schema_path",
    [
        "openviking/prompts/templates/memory/entities.yaml",
        "openviking/prompts/templates/memory/experimental_memory/entities.yaml",
    ],
)
def test_entity_filename_is_case_insensitive(schema_path):
    schema = yaml.safe_load(Path(schema_path).read_text(encoding="utf-8"))
    template = Template(schema["filename_template"])

    lower_path = template.render(category="concept", name="smart")
    upper_path = template.render(category="CONCEPT", name="SMART")

    assert lower_path == upper_path == "concept/smart.md"
