# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from openviking.parse.parsers.code.ast import extract_skeleton_result
from openviking.parse.parsers.code.ast.providers import is_skeleton_useful
from openviking.parse.parsers.code.ast.aider_repomap import (
    _clean_repromap_rendered,
    _definition_lines,
    _query_captures,
)
from openviking.parse.parsers.code.ast.process_engine import (
    extract_process_skeleton,
    supports_process_skeleton,
)


def test_tags_query_wins_without_calling_process(monkeypatch):
    process = Mock()
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers._extract_with_grep_ast",
        lambda *_args, **_kwargs: "# sample.cpp\n\nclass Widget",
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_process_skeleton",
        process,
    )

    result = extract_skeleton_result("sample.cpp", "class Widget {};")

    assert result.provider == "aider_repomap"
    assert not result.should_fallback_to_llm
    process.assert_not_called()


def test_low_quality_tags_request_llm_fallback_without_calling_process(monkeypatch):
    process = Mock()
    process.return_value = "# sample.cpp [C/C++]\n\nclass Widget"
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers._extract_with_grep_ast",
        lambda *_args, **_kwargs: "# sample.cpp",
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_process_skeleton",
        process,
    )

    result = extract_skeleton_result("sample.cpp", "class Widget {};")

    assert result.text is None
    assert result.provider == "llm"
    assert result.should_fallback_to_llm
    assert result.reason == "tags query produced no useful skeleton"
    process.assert_not_called()


def test_no_tags_uses_process(monkeypatch):
    process = Mock()
    process.return_value = "# sample.py [Python]\n\ndef run()"
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: False,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_process_skeleton",
        process,
    )

    result = extract_skeleton_result("sample.py", "def run():\n    pass\n")

    assert result.provider == "process"
    assert result.text


def test_both_extractors_unavailable_requests_llm_fallback(monkeypatch):
    process = Mock()
    process.return_value = None
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.has_tag_query",
        lambda _name: False,
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_process_skeleton",
        process,
    )

    result = extract_skeleton_result("component.vue", "<template />")

    assert result.text is None
    assert result.provider == "llm"
    assert result.should_fallback_to_llm


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# sample.py [Python]",
        "# sample.py [Python]\nimports: os, sys\nmodule: sample\nlanguage: Python",
    ],
)
def test_skeleton_usefulness_rejects_empty_or_metadata_only_output(text):
    assert not is_skeleton_useful(text)


@pytest.mark.parametrize(
    "text",
    [
        "# sample.lisp [aider-repomap-lite, compact]\n\n(defun add (a b)\n  (+ a b))",
        "# sample.ml [aider-repomap-lite, compact]\n\nlet add a b = a + b",
        "# sample.rkt [aider-repomap-lite, compact]\n\n(define (add a b)\n  (+ a b))",
    ],
)
def test_skeleton_usefulness_accepts_language_neutral_structure(text):
    assert is_skeleton_useful(text)


def test_clean_repromap_rendered_removes_tree_context_gutter():
    rendered = "⋮\n│struct Widget { int x; };\n│\n│int add(int a, int b) {\n│    return a + b;\n⋮"

    text = _clean_repromap_rendered(rendered)

    assert text == "struct Widget { int x; };\n\nint add(int a, int b) {\n    return a + b;"


def test_tags_query_rendered_skeleton_is_useful_after_cleaning(monkeypatch):
    process = Mock()
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_process_skeleton",
        process,
    )
    content = """#include <stdio.h>

struct Widget { int x; };

int add(int a, int b) {
    return a + b;
}

static void run(void) {
    printf("%d", add(1, 2));
}
"""

    result = extract_skeleton_result("sample.c", content)

    assert result.provider == "aider_repomap"
    assert not result.should_fallback_to_llm
    assert result.text
    assert "struct Widget" in result.text
    assert "int add(int a, int b)" in result.text
    assert "│" not in result.text
    assert "⋮" not in result.text
    process.assert_not_called()


@pytest.mark.parametrize("file_name", ["sample.cpp", "kernel.cu"])
def test_cpp_tags_capture_top_level_function_template_declarations_only(file_name):
    content = """#include <vector>

template <class T>
void reduce(int size, T *input, T *output);

int block_size;

void configure() {
    std::vector<int> buffer(block_size);
}
"""

    _, captures = _query_captures(file_name, content)
    lines = _definition_lines(captures)
    one_based_lines = {line + 1 for line in lines}

    assert 4 in one_based_lines
    assert 6 not in one_based_lines
    assert 9 not in one_based_lines


def test_ocaml_interface_tags_capture_interface_structures(monkeypatch):
    process = Mock()
    monkeypatch.setattr(
        "openviking.parse.parsers.code.ast.providers.extract_process_skeleton",
        process,
    )
    content = """type user = {
  id : string;
  name : string;
}

module type STORE = sig
  val find : string -> user option
end

module Cache : sig
  val get : string -> user option
end

class type printable = object
  method render : string
end

external hash_user : user -> int = "hash_user"

val create : id:string -> name:string -> user
"""

    result = extract_skeleton_result("sample.mli", content)

    assert result.provider == "aider_repomap"
    assert result.text
    assert "type user" in result.text
    assert "module type STORE" in result.text
    assert "module Cache" in result.text
    assert "class type printable" in result.text
    assert "external hash_user" in result.text
    assert "val create" in result.text
    process.assert_not_called()


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
    text = extract_process_skeleton(file_name, content)
    assert text is not None
    assert symbol in text


@pytest.mark.parametrize("file_name", ["README.md", "config.yaml", "data.json"])
def test_process_denylist(file_name):
    assert not supports_process_skeleton(file_name)


@pytest.mark.parametrize(
    "file_name",
    [
        "sample.py",
        "sample.ts",
        "sample.cpp",
        "sample.rs",
        "sample.go",
        "component.svelte",
        "schema.graphql",
        "schema.prisma",
        "main.tf",
        "query.sql",
        "service.proto",
    ],
)
def test_process_supported_code_languages(file_name):
    assert supports_process_skeleton(file_name)


@pytest.mark.parametrize(
    "file_name",
    [
        "README.md",
        "config.yaml",
        "data.json",
        "page.html",
        "style.css",
        "paper.tex",
        "chart.mmd",
        "diagram.dot",
        "request.http",
        "request.hurl",
    ],
)
def test_process_rejects_non_code_structured_formats(file_name):
    assert not supports_process_skeleton(file_name)
