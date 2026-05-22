# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the code-navigation pure functions backing the
code_outline / code_search / code_expand MCP tools."""

from openviking.parse.parsers.code.ast.code_tools import (
    expand_symbol,
    outline_file,
    search_symbols,
)
from openviking.parse.parsers.code.ast.extractor import get_extractor


PY_SAMPLE = '''"""Module top doc."""

import os
from typing import List


class Greeter:
    """Greets people."""

    def __init__(self, name: str):
        self.name = name

    def greet(self, who: str) -> str:
        """Return a greeting."""
        return f"Hello {who} from {self.name}"


def make_greeter(name: str) -> Greeter:
    return Greeter(name)
'''


# ---------------------------------------------------------------------------
# Line numbers populated by extractors
# ---------------------------------------------------------------------------


class TestLineNumbers:
    def test_python_class_and_method_lines(self):
        skel = get_extractor().extract("greeter.py", PY_SAMPLE)
        assert skel is not None
        assert len(skel.classes) == 1
        cls = skel.classes[0]
        assert cls.name == "Greeter"
        # Class spans from `class Greeter:` to end of greet body
        assert cls.line_start == 7
        assert cls.line_end >= 15

        method_names = [m.name for m in cls.methods]
        assert "__init__" in method_names
        assert "greet" in method_names

        greet = next(m for m in cls.methods if m.name == "greet")
        assert greet.line_start == 13
        assert greet.line_end == 15

    def test_python_top_level_function_lines(self):
        skel = get_extractor().extract("greeter.py", PY_SAMPLE)
        assert len(skel.functions) == 1
        fn = skel.functions[0]
        assert fn.name == "make_greeter"
        assert fn.line_start == 18
        assert fn.line_end == 19

    def test_unsupported_language_returns_none(self):
        assert get_extractor().extract("readme.md", "# hello") is None


GO_SAMPLE = """\
package main

type Greeter struct {
\tName string
}

func (g *Greeter) Greet(who string) string {
\treturn "Hello " + who
}

func MakeGreeter(name string) *Greeter {
\treturn &Greeter{Name: name}
}
"""

TS_SAMPLE = """\
class Greeter {
    name: string;
    constructor(name: string) {
        this.name = name;
    }
    greet(who: string): string {
        return `Hello ${who}`;
    }
}

function makeGreeter(name: string): Greeter {
    return new Greeter(name);
}
"""

RS_SAMPLE = """\
pub struct Greeter {
    pub name: String,
}

impl Greeter {
    pub fn greet(&self, who: &str) -> String {
        format!("Hello {}", who)
    }
}

pub fn make_greeter(name: String) -> Greeter {
    Greeter { name }
}
"""

JAVA_SAMPLE = """\
public class Greeter {
    private String name;

    public Greeter(String name) {
        this.name = name;
    }

    public String greet(String who) {
        return "Hello " + who;
    }
}
"""

CPP_SAMPLE = """\
#include <string>

class Greeter {
public:
    std::string greet(const std::string& who) {
        return "Hello " + who;
    }
};
"""

CS_SAMPLE = """\
public class Greeter {
    public string Name { get; set; }
    public string Greet(string who) {
        return "Hello " + who;
    }
}
"""

PHP_SAMPLE = """\
<?php

class Greeter {
    public $name;
    public function greet($who) {
        return "Hello $who";
    }
}

function makeGreeter($name) {
    return new Greeter();
}
"""

LUA_SAMPLE = """\
function Greeter:greet(who)
    return "Hello " .. who
end

function makeGreeter(name)
    return name
end
"""

_LANG_SAMPLES = [
    ("greeter.go", GO_SAMPLE, "Go"),
    ("greeter.ts", TS_SAMPLE, "TypeScript"),
    ("greeter.rs", RS_SAMPLE, "Rust"),
    ("Greeter.java", JAVA_SAMPLE, "Java"),
    ("greeter.cpp", CPP_SAMPLE, "C++"),
    ("Greeter.cs", CS_SAMPLE, "C#"),
    ("greeter.php", PHP_SAMPLE, "PHP"),
    ("greeter.lua", LUA_SAMPLE, "Lua"),
]


class TestLineNumbersAllLanguages:
    """Regression: every language extractor must populate non-zero line numbers.

    Prior to this commit all extractors left line_start/line_end at the default
    of 0.  These tests ensure the node.start_point / end_point wiring is correct
    for each supported language by asserting that every class and function in the
    sample snippet carries a positive, ordered span.
    """

    def _check_non_zero(self, file_name: str, src: str, lang_name: str):
        import pytest

        ext = get_extractor()
        if not ext.supports(file_name):
            pytest.skip(f"tree-sitter grammar for {lang_name} not installed")
        skel = ext.extract(file_name, src)
        assert skel is not None, f"{lang_name}: parse returned None"

        symbols = []
        for cls in skel.classes:
            symbols.append((f"class {cls.name}", cls.line_start, cls.line_end))
            for m in cls.methods:
                symbols.append((f"{cls.name}.{m.name}", m.line_start, m.line_end))
        for fn in skel.functions:
            symbols.append((f"fn {fn.name}", fn.line_start, fn.line_end))

        assert symbols, f"{lang_name}: no symbols extracted"
        for sym, start, end in symbols:
            assert start > 0, f"{lang_name} {sym}: line_start is 0 (not populated)"
            assert end >= start, f"{lang_name} {sym}: line_end {end} < line_start {start}"

    def test_go_line_numbers(self):
        self._check_non_zero("greeter.go", GO_SAMPLE, "Go")

    def test_typescript_line_numbers(self):
        self._check_non_zero("greeter.ts", TS_SAMPLE, "TypeScript")

    def test_rust_line_numbers(self):
        self._check_non_zero("greeter.rs", RS_SAMPLE, "Rust")

    def test_java_line_numbers(self):
        self._check_non_zero("Greeter.java", JAVA_SAMPLE, "Java")

    def test_cpp_line_numbers(self):
        self._check_non_zero("greeter.cpp", CPP_SAMPLE, "C++")

    def test_csharp_line_numbers(self):
        self._check_non_zero("Greeter.cs", CS_SAMPLE, "C#")

    def test_php_line_numbers(self):
        self._check_non_zero("greeter.php", PHP_SAMPLE, "PHP")

    def test_lua_line_numbers(self):
        self._check_non_zero("greeter.lua", LUA_SAMPLE, "Lua")


# ---------------------------------------------------------------------------
# outline_file
# ---------------------------------------------------------------------------


class TestOutlineFile:
    def test_outline_python(self):
        out = outline_file(PY_SAMPLE, "greeter.py")
        assert out.startswith("greeter.py  [Python,")
        assert "20 lines" in out  # 19 newlines + 1
        assert "imports: os, typing.List" in out
        assert 'module: "Module top doc."' in out
        assert "class Greeter  L7-" in out
        assert "+ __init__(self, name: str)  L10-11" in out
        assert "+ greet(self, who: str) -> str  L13-15" in out
        assert "def make_greeter(name: str) -> Greeter  L18-19" in out
        # outline must not leak docstrings (it's a navigation view)
        assert '"""' not in out
        assert "Return a greeting" not in out

    def test_outline_empty_file(self):
        # Empty content: no symbols, but the header should still appear
        out = outline_file("", "empty.py")
        assert out.startswith("empty.py  [Python,")
        # 0 newlines + 1 = 1 line in our convention; tolerated either way
        assert "lines]" in out

    def test_outline_unsupported_language(self):
        out = outline_file("# nothing", "notes.md")
        assert out == "Error: unsupported language for notes.md"


# ---------------------------------------------------------------------------
# search_symbols
# ---------------------------------------------------------------------------


SECOND_FILE = '''def greet():
    pass


class Other:
    def helper(self):
        pass
'''


class TestSearchSymbols:
    def test_substring_case_insensitive(self):
        result = search_symbols(
            "greet",
            [(PY_SAMPLE, "greeter.py"), (SECOND_FILE, "other.py")],
        )
        # Four substring hits on leaf name "greet":
        #   greeter.py : Greeter (class), Greeter.greet (method), make_greeter (fn)
        #   other.py   : greet (top-level fn)
        assert result.startswith('4 matches for "greet"')
        assert "scanned 2 files" in result
        assert "greeter.py" in result
        assert "other.py" in result
        assert "Greeter.greet" in result
        assert "make_greeter" in result

    def test_qualified_name_search(self):
        # Searching the full qualified name must also match.
        result = search_symbols("Greeter.greet", [(PY_SAMPLE, "greeter.py")])
        assert "1 matches" in result
        assert "Greeter.greet" in result

    def test_query_only_matches_leaf_name(self):
        result = search_symbols("helper", [(SECOND_FILE, "other.py")])
        assert "1 matches" in result
        assert "Other.helper" in result

    def test_no_match(self):
        result = search_symbols("nonexistent_xyz", [(PY_SAMPLE, "greeter.py")])
        assert result.startswith("No matches")
        assert "scanned 1 files" in result

    def test_empty_query(self):
        assert search_symbols("", [(PY_SAMPLE, "greeter.py")]) == "Error: empty query"

    def test_skips_unsupported_silently(self):
        # Markdown file is silently skipped (still counts toward scanned total)
        result = search_symbols(
            "greet",
            [(PY_SAMPLE, "greeter.py"), ("# heading", "notes.md")],
        )
        assert "scanned 2 files" in result
        assert "notes.md" not in result


# ---------------------------------------------------------------------------
# expand_symbol
# ---------------------------------------------------------------------------


class TestExpandSymbol:
    def test_expand_top_level_function(self):
        out = expand_symbol(PY_SAMPLE, "greeter.py", "make_greeter")
        assert out.startswith("# greeter.py  L18-19  (make_greeter)")
        assert "def make_greeter(name: str) -> Greeter:" in out
        assert "return Greeter(name)" in out

    def test_expand_class(self):
        out = expand_symbol(PY_SAMPLE, "greeter.py", "Greeter")
        assert "(Greeter)" in out
        assert "class Greeter:" in out
        assert "def greet" in out  # body included

    def test_expand_qualified_method(self):
        out = expand_symbol(PY_SAMPLE, "greeter.py", "Greeter.greet")
        assert "(Greeter.greet)" in out
        assert "def greet(self, who: str) -> str:" in out
        # Should NOT include __init__ or class header
        assert "class Greeter" not in out
        assert "__init__" not in out

    def test_expand_bare_method_resolves_to_first_match(self):
        # bare 'greet' should find the method via class walk (first match)
        out = expand_symbol(PY_SAMPLE, "greeter.py", "greet")
        assert "(Greeter.greet)" in out
        assert "def greet" in out

    def test_expand_missing_symbol(self):
        out = expand_symbol(PY_SAMPLE, "greeter.py", "does_not_exist")
        assert out == "Error: symbol 'does_not_exist' not found in greeter.py"

    def test_expand_unsupported_language(self):
        out = expand_symbol("# hello", "readme.md", "anything")
        assert out == "Error: unsupported language for readme.md"

    def test_expand_qualified_missing_class(self):
        out = expand_symbol(PY_SAMPLE, "greeter.py", "NoSuchClass.greet")
        assert "symbol 'NoSuchClass.greet' not found" in out
