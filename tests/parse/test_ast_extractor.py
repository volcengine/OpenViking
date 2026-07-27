# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from openviking.parse.parsers.code.ast import extract_skeleton, extract_skeleton_result
from openviking.parse.parsers.code.ast.aider_repomap import extract_repromap_skeleton
from openviking.parse.parsers.code.ast.extractor import (
    get_process_extractor,
    supports_code_skeleton,
)
from openviking.parse.parsers.code.ast.providers import extract_skeleton_with_routing


def test_tags_query_wins_without_calling_process(monkeypatch):
    process = Mock()
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_repromap_skeleton",
        lambda *_args, **_kwargs: "# sample.cpp\n\nclass Widget",
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.get_process_extractor",
        lambda: process,
    )

    result = extract_skeleton_with_routing("sample.cpp", "class Widget {};")

    assert result.provider == "aider_repomap"
    assert not result.should_fallback_to_llm
    process.extract_skeleton.assert_not_called()


def test_low_quality_tags_fall_through_to_process(monkeypatch):
    process = Mock()
    process.extract_skeleton.return_value = "# sample.cpp [C/C++]\n\nclass Widget"
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_repromap_skeleton",
        lambda *_args, **_kwargs: "# sample.cpp",
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.get_process_extractor",
        lambda: process,
    )

    result = extract_skeleton_with_routing("sample.cpp", "class Widget {};")

    assert result.provider == "process"
    assert not result.should_fallback_to_llm


def test_no_tags_uses_process(monkeypatch):
    process = Mock()
    process.extract_skeleton.return_value = "# sample.py [Python]\n\ndef run()"
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: False,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.get_process_extractor",
        lambda: process,
    )

    result = extract_skeleton_result("sample.py", "def run():\n    pass\n")

    assert result.provider == "process"
    assert result.text
    assert extract_skeleton("sample.py", "def run():\n    pass\n")


def test_both_extractors_unavailable_requests_llm_fallback(monkeypatch):
    process = Mock()
    process.extract_skeleton.return_value = None
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: False,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.get_process_extractor",
        lambda: process,
    )

    result = extract_skeleton_with_routing("component.vue", "<template />")

    assert result.text is None
    assert result.provider == "llm"
    assert result.should_fallback_to_llm


@pytest.mark.parametrize(
    ("file_name", "content", "symbol"),
    [
        ("sample.py", "def build_order():\n    return 1\n", "build_order"),
        ("sample.rb", "def build_order\n  1\nend\n", "build_order"),
        ("sample.swift", "func buildOrder() -> Int { return 1 }\n", "buildOrder"),
        ("sample.ts", "export function buildOrder(): number { return 1; }\n", "buildOrder"),
    ],
)
def test_process_smoke(file_name, content, symbol):
    text = get_process_extractor().extract_skeleton(file_name, content)
    assert text is not None
    assert symbol in text


@pytest.mark.parametrize("file_name", ["README.md", "config.yaml", "data.json"])
def test_process_denylist(file_name):
    assert not get_process_extractor().supports(file_name)


def test_viking_resource_path_recovers_parent_suffix():
    file_name = "viking://resources/sample.py/sample.md"
    assert supports_code_skeleton(file_name)
    text = get_process_extractor().extract_skeleton(file_name, "def run():\n    pass\n")
    assert text is not None
    assert "run" in text


def test_c_tags_render_signatures_without_function_bodies():
    content = """
struct User {
    int id;
};

static int add(int a, int b)
{
    int value = a + b;
    return value;
}

void run(void) { do_work(); }
"""

    text = extract_repromap_skeleton("sample.c", content)

    assert text is not None
    assert "struct User" in text
    assert "static int add(int a, int b)" in text
    assert "void run(void)" in text
    assert "return value" not in text
    assert "do_work" not in text
