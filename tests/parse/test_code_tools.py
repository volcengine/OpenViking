# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the code-navigation pure functions backing the
code_outline / code_search / code_expand MCP tools."""

from openviking.parse.parsers.code.ast.code_tools import (
    CODE_SEARCH_FILE_CAP,
    CodeLocateFile,
    _CodeLocateHit,
    _diagnostic_phrase_bonus,
    _format_verification_section,
    _is_diagnostic_assertion_line,
    expand_symbol,
    format_locate_json_text,
    locate_code,
    locate_code_structured,
    outline_file,
    search_code,
    search_symbols,
    select_code_uris,
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
# search_code
# ---------------------------------------------------------------------------


PYLINT_MISC_SAMPLE = '''class EncodingChecker:
    msgs = {
        "W0511": ("%s", "fixme", "Used when a warning note as FIXME or XXX is detected."),
    }

    def open(self):
        notes = "|".join(re.escape(note) for note in self.config.notes)
        regex_string = rf"#\\s*({notes})\\b"
        self._fixme_pattern = re.compile(regex_string, re.I)
'''


PYLINT_TEST_SAMPLE = '''class TestFixme:
    def test_fixme_with_message(self):
        code = "# FIXME message"

    def test_todo_without_message(self):
        code = "# TODO"
'''


class TestSearchCode:
    def test_select_code_uris_prioritizes_query_path_terms_before_cap(self):
        entries = [
            {"uri": f"viking://r/pylint/checkers/generic_{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP + 10)
        ]
        entries.append({"uri": "viking://r/pylint/checkers/misc.py", "isDir": False})

        uris, capped = select_code_uris(entries, "fixme notes misc")

        assert capped is True
        assert "viking://r/pylint/checkers/misc.py" in uris

    def test_select_code_uris_keeps_related_tests_for_selected_implementation_files(self):
        entries = [
            {"uri": f"viking://r/sklearn/generic_changed_{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP - 1)
        ]
        entries.extend(
            [
                {"uri": "viking://r/sklearn/utils/_pprint.py", "isDir": False},
                {"uri": "viking://r/sklearn/utils/tests/test_pprint.py", "isDir": False},
                {"uri": "viking://r/sklearn/z_other.py", "isDir": False},
            ]
        )

        uris, capped = select_code_uris(entries, "print_changed_only repr vector values")

        assert capped is True
        assert "viking://r/sklearn/utils/_pprint.py" in uris
        assert "viking://r/sklearn/utils/tests/test_pprint.py" in uris
        assert len(uris) == CODE_SEARCH_FILE_CAP

    def test_select_code_uris_uses_unified_cap_for_diagnostic_queries(self):
        entries = [
            {"uri": f"viking://r/sphinx/builders/latex/generated_{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP)
        ]
        entries.append({"uri": "viking://r/sphinx/domains/std.py", "isDir": False})

        uris, capped = select_code_uris(
            entries,
            "get_fignumber table no number is assigned numref warning table singlehtml latex std",
        )

        assert capped is True
        assert len(uris) == CODE_SEARCH_FILE_CAP
        assert "viking://r/sphinx/domains/std.py" in uris

    def test_hybrid_ranks_content_and_path_hits_with_symbol_hits(self):
        result = search_code(
            "fixme W0511 notes misc",
            [
                (PYLINT_TEST_SAMPLE, "viking://r/tests/checkers/unittest_misc.py"),
                ("* Fix a false positive regarding W0511.", "viking://r/ChangeLog"),
                (PYLINT_MISC_SAMPLE, "viking://r/pylint/checkers/misc.py"),
            ],
        )

        assert result.startswith('3 code matches for "fixme W0511 notes misc"')
        assert "path matches: misc" in result
        assert "symbols: EncodingChecker" in result
        assert 'L3: "W0511":' in result
        assert "L7: notes =" in result
        assert "viking://r/pylint/checkers/misc.py" in result
        assert "viking://r/tests/checkers/unittest_misc.py" in result
        assert result.index("viking://r/pylint/checkers/misc.py") < result.index(
            "viking://r/tests/checkers/unittest_misc.py"
        )
        assert result.index("viking://r/pylint/checkers/misc.py") < result.index(
            "viking://r/ChangeLog"
        )

    def test_hybrid_prioritizes_diagnostic_emitter_over_builder_path_noise(self):
        std_content = """\
class StandardDomain:
    def get_fignumber(self):
        return ()

    def _resolve_numref_xref(self):
        logger.warning("no number is assigned for %s: %s")
"""
        latex_content = """\
class Table:
    def get_table_type(self): pass
    def visit_table(self): pass
    def depart_table(self): pass
import warnings
# latex table singlehtml numref table latex
"""

        result = search_code(
            "get_fignumber table no number is assigned numref warning table singlehtml latex",
            [
                (latex_content, "viking://r/sphinx/writers/latex.py"),
                (std_content, "viking://r/sphinx/domains/std.py"),
            ],
        )

        assert "Diagnostic search note" in result
        assert "builder/path-only matches as context" in result
        assert result.index("viking://r/sphinx/domains/std.py") < result.index(
            "viking://r/sphinx/writers/latex.py"
        )

    def test_hybrid_prefers_implementation_for_non_test_issue_terms(self):
        result = search_code(
            "fixme",
            [
                (PYLINT_TEST_SAMPLE, "viking://r/tests/checkers/unittest_misc.py"),
                (PYLINT_MISC_SAMPLE, "viking://r/pylint/checkers/misc.py"),
            ],
        )

        assert "viking://r/pylint/checkers/misc.py" in result
        assert "viking://r/tests/checkers/unittest_misc.py" in result
        assert result.index("viking://r/pylint/checkers/misc.py") < result.index(
            "viking://r/tests/checkers/unittest_misc.py"
        )


# ---------------------------------------------------------------------------
# locate_code
# ---------------------------------------------------------------------------


class TestLocateCode:
    def test_locate_code_structured_returns_local_locations_without_viking_paths(self):
        result = locate_code_structured(
            "Fix W0511 fixme notes handling in the misc checker",
            [
                CodeLocateFile(
                    content=PYLINT_TEST_SAMPLE,
                    file_name="/repo/tests/checkers/unittest_misc.py",
                    location_type="local",
                    relative_path="tests/checkers/unittest_misc.py",
                ),
                CodeLocateFile(
                    content=PYLINT_MISC_SAMPLE,
                    file_name="/repo/pylint/checkers/misc.py",
                    location_type="local",
                    relative_path="pylint/checkers/misc.py",
                ),
            ],
            failing_tests=["test_fixme_with_message"],
        )

        assert result.edit_candidates[0].location == {
            "type": "local",
            "path": "/repo/pylint/checkers/misc.py",
            "relative_path": "pylint/checkers/misc.py",
        }
        assert "uri" not in result.edit_candidates[0].location
        assert result.behavior_references[0].location["type"] == "local"
        assert result.debug is None

    def test_locate_code_structured_returns_viking_locations_without_local_paths(self):
        result = locate_code_structured(
            "Fix W0511 fixme notes handling in the misc checker",
            [
                CodeLocateFile(PYLINT_TEST_SAMPLE, "viking://r/tests/checkers/unittest_misc.py"),
                CodeLocateFile(PYLINT_MISC_SAMPLE, "viking://r/pylint/checkers/misc.py"),
            ],
            failing_tests=["test_fixme_with_message"],
            debug=True,
        )

        location = result.edit_candidates[0].location
        assert location == {
            "type": "viking",
            "uri": "viking://r/pylint/checkers/misc.py",
            "relative_path": "pylint/checkers/misc.py",
        }
        assert "path" not in location
        assert result.debug is not None
        assert result.debug["query_terms"]
        assert result.debug["ranking_signals"][0]["location"]["type"] == "viking"

    def test_locate_code_json_text_uses_schema_version_and_no_debug_by_default(self):
        result = locate_code_structured(
            "Fix greet behavior",
            [
                CodeLocateFile(
                    content=PY_SAMPLE,
                    file_name="/repo/greeter.py",
                    location_type="local",
                    relative_path="greeter.py",
                )
            ],
        )

        payload = format_locate_json_text(result)

        assert '"schema_version": "code-locate/v1"' in payload
        assert '"type": "local"' in payload
        assert '"path": "/repo/greeter.py"' in payload
        assert '"debug"' not in payload

    def test_locate_code_separates_edit_candidates_from_behavior_references(self):
        result = locate_code(
            "Fix W0511 fixme notes handling in the misc checker",
            [
                (PYLINT_TEST_SAMPLE, "viking://r/tests/checkers/unittest_misc.py"),
                ("* Fix a false positive regarding W0511.", "viking://r/ChangeLog"),
                (PYLINT_MISC_SAMPLE, "viking://r/pylint/checkers/misc.py"),
            ],
            failing_tests=["test_fixme_with_message"],
        )

        assert result.startswith("Likely edit locations:")
        assert "Useful behavior references:" in result
        assert "viking://r/pylint/checkers/misc.py" in result
        assert "viking://r/tests/checkers/unittest_misc.py" in result
        assert result.index("viking://r/pylint/checkers/misc.py") < result.index(
            "Useful behavior references:"
        )
        assert result.index("Useful behavior references:") < result.index(
            "viking://r/tests/checkers/unittest_misc.py"
        )
        assert "next: inspect current checkout lines; no web/upstream/git history" in result
        assert "viking://r/ChangeLog" not in result

    def test_locate_code_includes_compact_import_context(self):
        result = locate_code(
            "Fix greet behavior",
            [(PY_SAMPLE, "viking://r/greeter.py")],
        )

        assert "viking://r/greeter.py" in result
        assert "imports:" in result
        assert "os" in result
        assert "typing.List" in result

    def test_locate_code_ranks_specific_symbols_above_common_word_noise(self):
        noisy_content = "\n".join(
            [
                '"""This module contains methods in the sklearn package."""',
                "# in the an is with sklearn",
                "# in the an is with sklearn",
                "# in the an is with sklearn",
            ]
        )
        pprint_content = '''"""Pretty print estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for name, value in params.items():
        default = value
        if value != default:
            return {name: value}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def _pprint_estimator(self, object, stream, indent, allowance, context, level):
        return object.__repr__()
'''

        result = locate_code(
            "bug in print_changed_only in new repr: vector values truth value array ambiguous",
            [
                (noisy_content, "viking://r/sklearn/ensemble/gradient_boosting.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
            ],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/ensemble/gradient_boosting.py"
        )
        assert "focus: _changed_params" in result
        assert "if value != default" in result

    def test_locate_code_uses_failing_test_path_without_repeated_term_swamping(self):
        broad_model_file = "\n".join(
            [
                "import numpy as np",
                "",
                "class LargeModel:",
                "    def fit(self, array):",
            ]
            + [
                "        if array is None: raise ValueError('array model value')"
                for _ in range(80)
            ]
        )
        pprint_content = '''"""Pretty print estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for name, value in params.items():
        default = value
        if value != default:
            return {name: value}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def _pprint_estimator(self, object, stream, indent, allowance, context, level):
        return object.__repr__()
'''

        result = locate_code(
            "bug in print_changed_only in new repr: vector values truth value array ambiguous",
            [
                (broad_model_file, "viking://r/sklearn/linear_model/logistic.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
            ],
            failing_tests=["sklearn/utils/tests/test_pprint.py::test_changed_only"],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/linear_model/logistic.py"
        )
        assert "focus: _changed_params" in result

    def test_locate_code_ranks_exact_issue_tokens_without_failing_tests(self):
        vectorizer_content = "\n".join(
            [
                "class DictVectorizer:",
                "    def fit(self, X):",
            ]
            + [
                "        raise ValueError('Sample sequence needs vector values')"
                for _ in range(40)
            ]
        )
        pprint_content = '''"""Pretty print estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for name, value in params.items():
        default = value
        if value != default:
            return {name: value}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def __init__(self):
        self._changed_only = get_config()["print_changed_only"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            "bug in print_changed_only repr generation for vector-valued params",
            [
                (vectorizer_content, "viking://r/sklearn/feature_extraction/dict_vectorizer.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
            ],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/feature_extraction/dict_vectorizer.py"
        )

    def test_locate_code_prioritizes_exact_identifier_over_path_noise(self):
        vectorizer_content = "\n".join(
            [
                '"""Transforms lists of feature-value mappings to vectors."""',
                "",
                "class DictVectorizer:",
                "    def fit(self, X):",
            ]
            + [
                line
                for i in range(12)
                for line in (
                    f"    def method_{i}(self):",
                    "        return 'vector values only repr value vector'",
                )
            ]
            + [
                "        raise ValueError('Sample sequence needs vector values')"
                for _ in range(20)
            ]
        )
        pprint_content = '''"""Pretty print estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for k, v in params.items():
        init_params = estimator.__init__
        if (v != init_params[k] and
                not (is_scalar_nan(init_params[k]) and is_scalar_nan(v))):
            return {k: v}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def __init__(self):
        self._changed_only = get_config()["print_changed_only"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            "bug in print_changed_only in new repr: vector values. "
            "Reproduces with LogisticRegressionCV(Cs=np.array([0.1, 1])) after "
            "sklearn.set_config(print_changed_only=True): ValueError from ambiguous "
            "truth value of array. Need fix repr generation for vector/array-valued "
            "params in print_changed_only mode.",
            [
                (vectorizer_content, "viking://r/sklearn/feature_extraction/dict_vectorizer.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
            ],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/feature_extraction/dict_vectorizer.py"
        )

    def test_locate_code_ignores_reproducing_setup_for_exact_identifiers(self):
        config_content = '''"""Global configuration."""

_global_config = {"print_changed_only": False}


def get_config():
    return _global_config.copy()


def set_config(print_changed_only=None):
    if print_changed_only is not None:
        _global_config["print_changed_only"] = print_changed_only
'''
        pprint_content = '''"""Pretty print estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for k, v in params.items():
        init_params = estimator.__init__
        if (v != init_params[k] and
                not (is_scalar_nan(init_params[k]) and is_scalar_nan(v))):
            return {k: v}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def __init__(self):
        self._changed_only = get_config()["print_changed_only"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            "bug in print_changed_only repr logic for vector-valued parameters. "
            "Reproducing with LogisticRegressionCV(Cs=np.array([0.1, 1])) under "
            "sklearn.set_config(print_changed_only=True) raises ValueError.",
            [
                (config_content, "viking://r/sklearn/_config.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
            ],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/_config.py"
        )

    def test_locate_code_downranks_fenced_reproduction_code(self):
        config_content = '''"""Global configuration."""

_global_config = {"print_changed_only": False}


def get_config():
    return _global_config.copy()


def set_config(print_changed_only=None):
    if print_changed_only is not None:
        _global_config["print_changed_only"] = print_changed_only
'''
        logistic_content = '''"""Logistic regression estimators."""


class LogisticRegressionCV:
    def __repr__(self):
        return "LogisticRegressionCV(Cs=[0.1, 1])"
'''
        pprint_content = '''"""Pretty print estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for k, v in params.items():
        init_params = estimator.__init__
        if (v != init_params[k] and
                not (is_scalar_nan(init_params[k]) and is_scalar_nan(v))):
            return {k: v}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def __init__(self):
        self._changed_only = get_config()["print_changed_only"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            """bug in print_changed_only in new repr: vector values
```python
import sklearn
import numpy as np
from sklearn.linear_model import LogisticRegressionCV
sklearn.set_config(print_changed_only=True)
print(LogisticRegressionCV(Cs=np.array([0.1, 1])))
```
> ValueError: The truth value of an array with more than one element is ambiguous.
""",
            [
                (config_content, "viking://r/sklearn/_config.py"),
                (logistic_content, "viking://r/sklearn/linear_model/logistic.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
            ],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/_config.py"
        )
        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/linear_model/logistic.py"
        )

    def test_locate_code_downranks_agent_setup_context(self):
        config_content = '''"""Global configuration."""

_global_config = {"print_changed_only": False}


def get_config():
    return _global_config.copy()


def set_config(print_changed_only=None):
    if print_changed_only is not None:
        _global_config["print_changed_only"] = print_changed_only
'''
        pprint_content = '''"""BaseEstimator.__repr__ for pretty-printing estimators."""

from inspect import signature
import pprint
from collections import OrderedDict

from ..base import BaseEstimator
from .._config import get_config
from . import is_scalar_nan


def _changed_params(estimator):
    """Return parameters changed from their default values."""
    params = estimator.get_params(deep=False)
    for k, v in params.items():
        init_params = estimator.__init__
        if (v != init_params[k] and
                not (is_scalar_nan(init_params[k]) and is_scalar_nan(v))):
            return {k: v}
    return {}


class _EstimatorPrettyPrinter(pprint.PrettyPrinter):
    def __init__(self):
        self._changed_only = get_config()["print_changed_only"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''
        estimator_checks_content = "\n".join(
            [
                "def check_estimator(estimator):",
                "    return estimator",
            ]
            + [
                "def check_parameters_default_constructible(estimator):",
                "    if estimator.__class__.__name__ == 'LogisticRegressionCV':",
                "        raise ValueError('estimator parameter code path')",
            ]
            * 8
        )
        test_config_content = """\
from sklearn import get_config, set_config, config_context


def test_set_config():
    set_config(print_changed_only=True)
    assert get_config()["print_changed_only"]
"""
        test_pprint_content = """\
import numpy as np
from sklearn import set_config
from sklearn.linear_model import LogisticRegressionCV


def test_changed_only():
    set_config(print_changed_only=True)
    repr(LogisticRegressionCV(Cs=np.array([0.1, 1])))
"""

        result = locate_code(
            "Bug in `print_changed_only` repr for estimators with vector-valued "
            "parameters: `LogisticRegressionCV(Cs=np.array([0.1, 1]))` raises "
            "`ValueError: The truth value of an array with more than one element "
            "is ambiguous` when `sklearn.set_config(print_changed_only=True)` is "
            "enabled. Find the repr/pretty-print code path and likely edit locations.",
            [
                (config_content, "viking://r/sklearn/_config.py"),
                (estimator_checks_content, "viking://r/sklearn/utils/estimator_checks.py"),
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
                (test_config_content, "viking://r/sklearn/tests/test_config.py"),
                (test_pprint_content, "viking://r/sklearn/utils/tests/test_pprint.py"),
            ],
        )

        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/_config.py"
        )
        assert result.index("viking://r/sklearn/utils/_pprint.py") < result.index(
            "viking://r/sklearn/utils/estimator_checks.py"
        )
        assert result.index("viking://r/sklearn/utils/tests/test_pprint.py") < result.index(
            "viking://r/sklearn/tests/test_config.py"
        )

    def test_locate_code_suggests_narrow_verification_before_full_tests(self):
        pprint_content = """\
import numpy as np


def _changed_params(estimator):
    params = estimator.get_params(deep=False)
    for name, value in params.items():
        if value != estimator.default:
            return {name: value}
    return {}
"""
        test_pprint_content = """\
import numpy as np
from sklearn.linear_model import LogisticRegressionCV


def test_changed_only():
    repr(LogisticRegressionCV(Cs=np.array([0.1, 1])))
"""

        result = locate_code(
            "Bug in print_changed_only repr for vector-valued estimator params",
            [
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
                (test_pprint_content, "viking://r/sklearn/utils/tests/test_pprint.py"),
            ],
        )

        assert "Suggested verification:" in result
        assert "python3 -m py_compile sklearn/utils/_pprint.py" in result
        assert "python3 -m pytest sklearn/utils/tests/test_pprint.py" in result
        assert "If pytest fails before collection, treat as setup" in result

    def test_locate_code_suggested_verification_stays_minimal(self):
        pprint_content = """\
from sklearn._config import get_config


def _changed_params(estimator):
    if get_config()["print_changed_only"]:
        return repr(estimator)
    return {}
"""
        example_content = """\
from sklearn import set_config


set_config(print_changed_only=True)
print("changed only repr example")
"""
        test_pprint_content = """\
from sklearn import set_config


def test_changed_only():
    set_config(print_changed_only=True)
"""
        test_config_content = """\
from sklearn import get_config, set_config


def test_config_context():
    set_config(print_changed_only=True)
    assert get_config()["print_changed_only"]
"""

        result = locate_code(
            "Bug in print_changed_only repr for vector-valued estimator params",
            [
                (pprint_content, "viking://r/sklearn/utils/_pprint.py"),
                (example_content, "viking://r/examples/plot_changed_only_pprint_parameter.py"),
                (test_pprint_content, "viking://r/sklearn/utils/tests/test_pprint.py"),
                (test_config_content, "viking://r/sklearn/tests/test_config.py"),
            ],
        )

        static_line = next(line for line in result.splitlines() if line.startswith("- static:"))
        tests_line = next(line for line in result.splitlines() if line.startswith("- narrow tests:"))
        assert static_line == "- static: python3 -m py_compile sklearn/utils/_pprint.py"
        assert tests_line == "- narrow tests: python3 -m pytest sklearn/utils/tests/test_pprint.py"

    def test_locate_code_boosts_nearby_issue_terms_over_repeated_noise(self):
        noisy_order_content = "\n".join(
            [
                "class ListView:",
                "    def get_ordering(self):",
            ]
            + ["        return self.ordering  # order value" for _ in range(40)]
        )
        migration_content = """\
from django.db.migrations import operations


class MigrationAutodetector:
    def generate_created_models(self):
        operations.CreateModel(name="LookImage")
        operations.AddIndex(
            model_name="lookimage",
            index=models.Index(fields=["look", "_order"]),
        )
        operations.AlterOrderWithRespectTo(
            name="lookimage",
            order_with_respect_to="look",
        )
"""

        result = locate_code(
            "AlterOrderWithRespectTo with ForeignKey crash when _order is included "
            "in AddIndex. AddIndex of _order is emitted before "
            "AlterOrderWithRespectTo creates the _order field.",
            [
                (noisy_order_content, "viking://r/django/views/generic/list.py"),
                (migration_content, "viking://r/django/db/migrations/autodetector.py"),
            ],
        )

        assert result.index("viking://r/django/db/migrations/autodetector.py") < result.index(
            "viking://r/django/views/generic/list.py"
        )

    def test_locate_code_verification_guidance_bounds_agent_search(self):
        result = locate_code(
            "WARNING no number is assigned for table numref in singlehtml latex",
            [
                (
                    "def assign_figure_numbers(app):\n"
                    "    return {'table': app.config.numfig}\n",
                    "viking://r/sphinx/environment/collectors/toctree.py",
                ),
                (
                    "def test_numref_table_warning(app):\n"
                    "    assert 'table' in app.builder.fignumbers\n",
                    "viking://r/tests/test_build_html.py",
                ),
            ],
        )

        assert "current checkout" in result
        assert "Do not use web, upstream patches, or git log" in result

    def test_locate_code_structured_verification_guidance_bounds_agent_search(self):
        result = locate_code_structured(
            "WARNING no number is assigned for table numref in singlehtml latex",
            [
                CodeLocateFile(
                    content=(
                        "def assign_figure_numbers(app):\n"
                        "    return {'table': app.config.numfig}\n"
                    ),
                    file_name="/repo/sphinx/environment/collectors/toctree.py",
                    location_type="local",
                    relative_path="sphinx/environment/collectors/toctree.py",
                ),
                CodeLocateFile(
                    content=(
                        "def test_numref_table_warning(app):\n"
                        "    assert 'table' in app.builder.fignumbers\n"
                    ),
                    file_name="/repo/tests/test_build_html.py",
                    location_type="local",
                    relative_path="tests/test_build_html.py",
                ),
            ],
            source_root="/repo",
        )

        setup_note = next(item for item in result.verification if item["kind"] == "setup_note")
        assert "current checkout" in setup_note["reason"]
        assert "Do not use web, upstream patches, or git log" in setup_note["reason"]

    def test_locate_code_structured_uses_existing_fields_for_agent_harness_guidance(self):
        result = locate_code_structured(
            "WARNING no number is assigned for table numref in singlehtml latex",
            [
                CodeLocateFile(
                    content=(
                        "class StandardDomain:\n"
                        "    def _resolve_numref_xref(self, env, fromdocname, builder):\n"
                        "        return env.toc_fignumbers.get('table')\n"
                    ),
                    file_name="/repo/sphinx/domains/std.py",
                    location_type="local",
                    relative_path="sphinx/domains/std.py",
                ),
                CodeLocateFile(
                    content=(
                        "def test_numref_table_warning(app):\n"
                        "    assert 'table' in app.builder.fignumbers\n"
                    ),
                    file_name="/repo/tests/test_build_html.py",
                    location_type="local",
                    relative_path="tests/test_build_html.py",
                ),
            ],
            source_root="/repo",
        )

        payload = result.to_dict()
        assert "search_policy" not in payload
        assert "workflow" not in payload
        assert "current checkout" in result.edit_candidates[0].next_action
        assert "web" in result.edit_candidates[0].next_action
        assert "git log" in result.edit_candidates[0].next_action
        assert (
            "same-file or same-domain resolver/error-handling precedent"
            in result.edit_candidates[0].next_action
        )
        assert "first patch near the emitter guard" in result.edit_candidates[0].next_action
        assert "static check first" in result.edit_candidates[0].next_action
        assert "stop broad fixture search" in result.edit_candidates[0].next_action
        assert "preserve the diagnostic" in result.edit_candidates[0].next_action
        assert "wording or argument changes" in result.edit_candidates[0].next_action
        assert "fail-to-pass risk" in result.edit_candidates[0].next_action
        assert "message/arguments first" in result.edit_candidates[0].next_action
        assert "prefer the local diagnostic patch before broader implementation changes" in (
            result.edit_candidates[0].next_action
        )
        assert "lookup" not in result.edit_candidates[0].next_action
        assert "numbering rewrites" not in result.edit_candidates[0].next_action

    def test_locate_code_prioritizes_warning_emitter_and_assertions(self):
        std_content = """\
class StandardDomain:
    def _resolve_numref_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            fignumber = self.get_fignumber(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("no number is assigned for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def warn_missing_xref_title(self, target, node):
        msg = __("Failed to create a cross reference. A title or caption not found: %s")
        logger.warning(msg % target, location=node)
"""
        toctree_content = """\
class TocTreeCollector:
    def assign_figure_numbers(self, env):
        # Skip if uncaptioned node.
        if domain.name == "std" and not domain.get_numfig_title(node):
            return None
"""
        broad_writer_content = """\
class LaTeXTranslator:
    def visit_table(self, node):
        if self.builder.name == "latex":
            self.body.append("numfig table numbering")
        if self.builder.name == "singlehtml":
            self.body.append("assigned number changed for table")
        return "table numfig numbering assigned number latex singlehtml"
"""
        html_test_content = """\
def test_numfig_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings
"""
        latex_test_content = """\
def test_numfig_table_warning_is_not_emitted(app, warning):
    app.build()
    assert "WARNING: no number is assigned for table:" not in warning.getvalue()
"""
        root_conf_content = """\
import sphinx

numfig = True
latex_documents = [("index", "Sphinx.tex", "Sphinx", "Sphinx", "manual")]
keep_warnings = True
"""
        unrelated_test_content = """\
def test_intersphinx_warning(app, warning):
    assert "WARNING" in warning.getvalue()
"""

        result = locate_code_structured(
            "Sphinx 3.3 upgrade started generating "
            'warning "no number is assigned for table" when building '
            "singlehtml or latex with numref.",
            [
                CodeLocateFile(
                    toctree_content,
                    "viking://r/sphinx/environment/collectors/toctree.py",
                ),
                CodeLocateFile(std_content, "viking://r/sphinx/domains/std.py"),
                CodeLocateFile(broad_writer_content, "viking://r/sphinx/writers/latex.py"),
                CodeLocateFile(root_conf_content, "viking://r/tests/roots/test-root/conf.py"),
                CodeLocateFile(
                    unrelated_test_content,
                    "viking://r/tests/test_ext_intersphinx.py",
                ),
                CodeLocateFile(latex_test_content, "viking://r/tests/test_build_latex.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        assert result.edit_candidates[0].location["uri"] == "viking://r/sphinx/domains/std.py"
        assert (
            result.behavior_references[0].location["uri"]
            == "viking://r/tests/test_build_html.py"
        )
        assert result.edit_candidates[0].reasons[0].startswith("diagnostic signal")
        assert result.behavior_references[0].reasons[0].startswith("diagnostic signal")
        assert "positive diagnostic assertion" in result.behavior_references[0].reasons[1]
        assert any(
            "asserted diagnostic wording differs from issue" in reason
            for reason in result.behavior_references[0].reasons
        )
        assert any(
            "no number is assigned for section" in snippet["text"]
            for snippet in result.behavior_references[0].snippets
        )
        assert any(
            "same-file diagnostic precedent" in snippet["text"]
            and "Failed to create a cross reference" in snippet["text"]
            for snippet in result.edit_candidates[0].snippets
        )
        assert any(
            "same-file diagnostic precedent found" in reason
            for reason in result.edit_candidates[0].reasons
        )
        assert "diagnostic wording delta" in result.edit_candidates[0].next_action
        assert "not a numbering/builder regression" in result.edit_candidates[0].next_action
        assert "production diagnostic emitter" in result.edit_candidates[0].next_action
        assert "same-file diagnostic precedent prefix/style" in (
            result.edit_candidates[0].next_action
        )
        assert "reason semantics" in result.edit_candidates[0].next_action
        assert "if it passes" in result.edit_candidates[0].next_action
        assert "final-answer immediately" in result.edit_candidates[0].next_action
        assert "do not inspect visible tests" in result.edit_candidates[0].next_action
        assert "get_fignumber" not in result.edit_candidates[0].next_action
        assert "toc_fignumbers" not in result.edit_candidates[0].next_action
        assert len(result.edit_candidates) == 1
        assert len(result.behavior_references) == 1
        assert result.verification[0]["targets"][0]["relative_path"] == "sphinx/domains/std.py"
        assert all(
            item.get("kind") != "narrow_tests" or item.get("command") is None
            for item in result.verification
        )

    def test_locate_code_prefers_warning_emitter_for_full_sphinx_issue(self):
        std_content = """\
class StandardDomain:
    def _resolve_numref_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            fignumber = self.get_fignumber(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("no number is assigned for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def get_fignumber(self, env, builder, figtype, docname, target_node):
        return env.toc_fignumbers[docname][figtype][target_node["ids"][0]]
"""
        toctree_content = """\
class TocTreeCollector:
    def assign_section_numbers(self, env):
        if env.config.master_doc in env.numbered_toctrees:
            logger.warning(__("%s is already assigned section numbers"),
                           env.config.master_doc)

    def assign_figure_numbers(self, env):
        for docname in env.found_docs:
            numbers = {}
            for figtype in ("figure", "table", "code-block"):
                numbers[figtype] = self.get_fignumbers(env, docname, figtype)
            env.toc_fignumbers[docname] = numbers

    def get_fignumbers(self, env, docname, figtype):
        if figtype == "table":
            return {"id1": (1,)}
        return {}
"""
        html_test_content = """\
def test_numfig_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings
"""

        result = locate_code_structured(
            'v3.3 upgrade started generating "WARNING: no number is assigned for table" '
            "warnings. We've updated to Sphinx 3.3 in our documentation, and suddenly "
            "the following warning started popping up in our builds when we build either "
            "`singlehtml` or `latex`: `WARNING: no number is assigned for table:`. "
            "I looked through the changelog but it didn't seem like there was anything "
            "related to `numref` that was changed. Could anyone point me to a change in "
            "the numref logic so I can figure out where these warnings are coming from?",
            [
                CodeLocateFile(toctree_content, "viking://r/sphinx/environment/collectors/toctree.py"),
                CodeLocateFile(std_content, "viking://r/sphinx/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        assert result.edit_candidates[0].location["uri"] == "viking://r/sphinx/domains/std.py"
        assert result.verification[0]["targets"][0]["relative_path"] == "sphinx/domains/std.py"
        assert any(
            "asserted diagnostic wording differs from issue" in reason
            for reason in result.behavior_references[0].reasons
        )

    def test_locate_code_promotes_diagnostic_wording_delta_to_action(self):
        std_content = """\
class StandardDomain:
    def _resolve_numref_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            fignumber = self.get_fignumber(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("no number is assigned for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def warn_missing_xref_title(self, target, node):
        msg = __("Failed to create a cross reference. A title or caption not found: %s")
        logger.warning(msg % target, location=node)
"""
        html_test_content = """\
def test_numfig_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings
"""
        singlehtml_content = """\
class SingleFileHTMLBuilder:
    name = "singlehtml"

    def assemble_toc_fignumbers(self):
        # singlehtml latex table builder numbering numref assigned number
        return {"index": {"table": {"id1": (1,)}}}
"""
        latex_test_content = """\
def test_numref_with_latex_builder(app, warning):
    app.build()
    assert "latex" in app.builder.name
"""

        result = locate_code_structured(
            "Sphinx 3.3 upgrade started generating warning "
            '"no number is assigned for table" for singlehtml or latex builds; '
            "likely numref/table numbering logic regression",
            [
                CodeLocateFile(std_content, "viking://r/sphinx/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
                CodeLocateFile(singlehtml_content, "viking://r/sphinx/builders/singlehtml.py"),
                CodeLocateFile(latex_test_content, "viking://r/tests/test_build_latex.py"),
            ],
            allow_viking_commands=True,
        )

        assert "wording delta" in result.summary_text
        assert "patch diagnostic message/arguments first" in result.summary_text
        assert "as behavior references" in result.summary_text
        assert "python3 -m py_compile sphinx/domains/std.py" in result.summary_text
        assert "python3 -m pytest tests/test_build_html.py" not in result.summary_text
        assert "positive warning assertion means preserve the diagnostic first" in result.summary_text
        assert "same-file diagnostic precedent prefix/style" in result.summary_text
        assert "reason semantics" in result.summary_text
        assert "report-only terms are context" in result.summary_text
        assert "if that static check passes, stop" in result.summary_text
        assert "continue broader discovery only if that immediate path fails" in result.summary_text
        assert "get_fignumber" not in result.summary_text
        assert "toc_fignumbers" not in result.summary_text
        assert "diagnostic wording delta" in result.edit_candidates[0].next_action
        assert result.edit_candidates[0].next_action.startswith("PATCH FIRST:")
        assert "production diagnostic emitter" in result.edit_candidates[0].next_action
        assert "do not edit tests, assertions, fixtures, builders, or numbering logic" in (
            result.edit_candidates[0].next_action
        )
        assert "not a numbering/builder regression" in result.edit_candidates[0].next_action
        assert "same-file diagnostic precedent prefix/style" in (
            result.edit_candidates[0].next_action
        )
        assert "reason semantics" in result.edit_candidates[0].next_action
        assert "if patch application fails" in result.edit_candidates[0].next_action
        assert "if it passes" in result.edit_candidates[0].next_action
        assert "final-answer immediately" in result.edit_candidates[0].next_action
        assert "not a numbering/builder regression" in result.edit_candidates[0].next_action
        assert "get_fignumber" not in result.edit_candidates[0].next_action
        assert "toc_fignumbers" not in result.edit_candidates[0].next_action
        assert "same-file or same-domain" not in result.edit_candidates[0].next_action
        assert "unless local evidence proves" not in result.edit_candidates[0].next_action
        assert len(result.edit_candidates) == 1
        assert len(result.behavior_references) == 1
        assert any(
            "same-file diagnostic precedent" in snippet["text"]
            for snippet in result.edit_candidates[0].snippets
        )

    def test_locate_code_detects_unquoted_warning_wording_delta(self):
        std_content = """\
class StandardDomain:
    def _resolve_numref_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            fignumber = self.get_fignumber(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("no number is assigned for %s: %s"), "table", "id1",
                           location=node)
            return contnode
"""
        html_test_content = """\
def test_numfig_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings
"""
        latex_test_content = """\
def test_numref_table_caption(app):
    result = app.outdir.joinpath("python.tex").read_text()
    assert "\\\\sphinxcaption{The table title with a reference" in result
"""

        result = locate_code_structured(
            "Sphinx 3.3 upgrade started generating warning: no number is assigned "
            "for table in singlehtml or latex builds, likely numref logic change",
            [
                CodeLocateFile(std_content, "viking://r/sphinx/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
                CodeLocateFile(latex_test_content, "viking://r/tests/test_build_latex.py"),
            ],
            allow_viking_commands=True,
        )

        assert "wording delta" in result.summary_text
        assert len(result.edit_candidates) == 1
        assert result.edit_candidates[0].location["uri"] == "viking://r/sphinx/domains/std.py"
        assert len(result.behavior_references) == 1
        assert (
            result.behavior_references[0].location["uri"]
            == "viking://r/tests/test_build_html.py"
        )

    def test_locate_code_keeps_multiple_positive_diagnostic_assertions(self):
        std_content = """\
class StandardDomain:
    def _resolve_numref_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            fignumber = self.get_fignumber(env, builder, "section", "index", node)
        except ValueError:
            logger.warning(__("no number is assigned for %s: %s"), "section", "index",
                           location=node)
            return contnode

    def warn_missing_xref_title(self, target, node):
        msg = __("Failed to create a cross reference. A title or caption not found: %s")
        logger.warning(msg % target, location=node)
"""
        html_test_content = """\
def test_numfig_without_numbered_toctree_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings

def test_numfig_with_numbered_toctree_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings

def test_numfig_with_prefix_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings

def test_numfig_with_secnum_depth_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings
"""

        result = locate_code_structured(
            "Sphinx 3.3 started generating warning: no number is assigned for table "
            "during singlehtml or latex numref builds",
            [
                CodeLocateFile(std_content, "viking://r/sphinx/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        reference = result.behavior_references[0]
        warning_snippets = [
            snippet
            for snippet in reference.snippets
            if "no number is assigned for section" in snippet["text"]
        ]
        assert len(warning_snippets) == 4

    def test_locate_code_keeps_diagnostic_snippets_on_emitter_when_wording_differs(self):
        std_content = """\
class StandardDomain:
    def _resolve_numref_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            fignumber = self.get_fignumber(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("no number is assigned for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def get_fignumber(self, env, builder, figtype, docname, target_node):
        if builder.name == "latex":
            return env.toc_fignumbers[docname][figtype][target_node["ids"][0]]
        return ()
"""
        html_test_content = """\
def test_numfig_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: no number is assigned for section: index" in warnings
"""

        result = locate_code_structured(
            "Sphinx 3.3 upgrade started generating "
            "'WARNING: no number is assigned for table' warnings in "
            "singlehtml or latex builds; likely numref logic around tables and warnings",
            [
                CodeLocateFile(std_content, "viking://r/sphinx/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        top = result.edit_candidates[0]
        assert any("logger.warning" in snippet["text"] for snippet in top.snippets)
        assert all("builder.name" not in snippet["text"] for snippet in top.snippets)
        assert all(
            symbol["name"] != "StandardDomain.get_fignumber"
            for symbol in top.focus_symbols
        )

    def test_diagnostic_phrase_bonus_distinguishes_inverse_warning_meaning(self):
        assert (
            _diagnostic_phrase_bonus(
                'logger.warning(__("no number is assigned for %s: %s"), figtype, labelid)',
                {"number", "assigned"},
            )
            > 0
        )
        assert (
            _diagnostic_phrase_bonus(
                "logger.warning(__('%s is already assigned section numbers'), ref)",
                {"number", "assigned"},
            )
            == 0
        )

    def test_plain_render_assertion_is_not_diagnostic_assertion(self):
        assert not _is_diagnostic_assertion_line(
            r"assert '\\sphinxcaption{The table title with a reference' in result"
        )

    def test_suggested_verification_prefers_edit_matching_top_behavior_test(self):
        lines = _format_verification_section(
            [
                _CodeLocateHit(file_name="viking://r/sklearn/externals/_arff.py", score=200),
                _CodeLocateHit(file_name="viking://r/sklearn/utils/_pprint.py", score=180),
            ],
            [
                _CodeLocateHit(
                    file_name="viking://r/sklearn/utils/tests/test_pprint.py",
                    score=300,
                )
            ],
        )

        assert "- static: python3 -m py_compile sklearn/utils/_pprint.py" in lines


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


# ---------------------------------------------------------------------------
# filter_code_uris
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from openviking.parse.parsers.code.ast.code_tools import (  # noqa: E402
    filter_code_uris,
)


class TestFilterCodeUris:
    def test_keeps_supported_extensions(self):
        entries = [
            {"uri": "viking://r/a.py", "isDir": False},
            {"uri": "viking://r/b.md", "isDir": False},
            {"uri": "viking://r/c.ts", "isDir": False},
            {"uri": "viking://r/d.txt", "isDir": False},
        ]
        uris, capped = filter_code_uris(entries)
        assert uris == ["viking://r/a.py", "viking://r/c.ts"]
        assert capped is False

    def test_skips_directories(self):
        entries = [
            {"uri": "viking://r/sub", "isDir": True},
            {"uri": "viking://r/a.py", "isDir": False},
        ]
        uris, capped = filter_code_uris(entries)
        assert uris == ["viking://r/a.py"]
        assert capped is False

    def test_object_entries_snake_case(self):
        entries = [
            SimpleNamespace(uri="viking://r/a.py", is_dir=False),
            SimpleNamespace(uri="viking://r/sub", is_dir=True),
        ]
        uris, capped = filter_code_uris(entries)
        assert uris == ["viking://r/a.py"]
        assert capped is False

    def test_exactly_cap_not_capped(self):
        entries = [
            {"uri": f"viking://r/f{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP)
        ]
        uris, capped = filter_code_uris(entries)
        assert len(uris) == CODE_SEARCH_FILE_CAP
        assert capped is False

    def test_one_file_over_cap_triggers_cap(self):
        entries = [
            {"uri": f"viking://r/f{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP + 1)
        ]
        uris, capped = filter_code_uris(entries)
        assert len(uris) == CODE_SEARCH_FILE_CAP
        assert capped is True

    def test_empty_entries(self):
        uris, capped = filter_code_uris([])
        assert uris == []
        assert capped is False
