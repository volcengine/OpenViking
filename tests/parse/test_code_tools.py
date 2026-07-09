# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the code-navigation pure functions backing the
code_outline / code_search / code_expand MCP tools."""

from openviking.parse.parsers.code.ast.code_tools import (
    CODE_SEARCH_FILE_CAP,
    CodeLocateFile,
    CodeLocateHints,
    _CodeLocateHit,
    _diagnostic_phrase_bonus,
    _format_verification_section,
    _is_diagnostic_assertion_line,
    expand_symbol,
    format_locate_json_text,
    format_locate_text,
    locate_code,
    locate_code_structured,
    locate_selection_query,
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
            {"uri": f"viking://r/samplepkg/generic_changed_{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP - 1)
        ]
        entries.extend(
            [
                {"uri": "viking://r/samplepkg/utils/pretty.py", "isDir": False},
                {"uri": "viking://r/samplepkg/utils/tests/test_pretty.py", "isDir": False},
                {"uri": "viking://r/samplepkg/z_other.py", "isDir": False},
            ]
        )

        uris, capped = select_code_uris(entries, "compact_repr repr vector values")

        assert capped is True
        assert "viking://r/samplepkg/utils/pretty.py" in uris
        assert "viking://r/samplepkg/utils/tests/test_pretty.py" in uris
        assert len(uris) == CODE_SEARCH_FILE_CAP

    def test_select_code_uris_uses_unified_cap_for_diagnostic_queries(self):
        entries = [
            {"uri": f"viking://r/docsuite/builders/pdf/generated_{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP)
        ]
        entries.append({"uri": "viking://r/docsuite/domains/std.py", "isDir": False})

        uris, capped = select_code_uris(
            entries,
            "get_resource_id table resource id is missing docref warning table singlepage pdf std",
        )

        assert capped is True
        assert len(uris) == CODE_SEARCH_FILE_CAP
        assert "viking://r/docsuite/domains/std.py" in uris

    def test_locate_selection_query_expands_structured_hints_for_prefiltering(self):
        entries = [
            {"uri": f"viking://r/pkg/noise_{i}.py", "isDir": False}
            for i in range(5)
        ]
        entries.append({"uri": "viking://r/pkg/serializers.py", "isDir": False})

        selection_query = locate_selection_query(
            "Regression in output.",
            terms=["flag"],
            hints=CodeLocateHints(paths=["serializers.py"], symbols=["serialize_flag"]),
        )
        uris, capped = select_code_uris(entries, selection_query, cap=3)

        assert capped is True
        assert "serializers.py" in selection_query
        assert "serialize_flag" in selection_query
        assert "viking://r/pkg/serializers.py" in uris

    def test_select_code_uris_prefers_implementation_files_for_locate_cap(self):
        entries = [
            {"uri": f"viking://r/tests/migrations/test_generated_{i}.py", "isDir": False}
            for i in range(20)
        ]
        entries.extend(
            [
                {"uri": "viking://r/webfw/db/migrations/autodetector.py", "isDir": False},
                {"uri": "viking://r/webfw/db/migrations/operations/models.py", "isDir": False},
            ]
        )

        uris, capped = select_code_uris(
            entries,
            "migration relative_order_field AddIndex _order",
            cap=5,
            prefer_implementation=True,
            priority_terms=["migrations"],
        )

        assert capped is True
        assert "viking://r/webfw/db/migrations/autodetector.py" in uris
        assert "viking://r/webfw/db/migrations/operations/models.py" in uris

    def test_select_code_uris_honors_explicit_locate_path_hints_before_cap(self):
        entries = [
            {"uri": f"viking://r/tests/queries/test_generated_{i}.py", "isDir": False}
            for i in range(20)
        ]
        entries.append({"uri": "viking://r/webfw/db/models/query_utils.py", "isDir": False})

        uris, capped = select_code_uris(
            entries,
            "cannot pickle Q object",
            cap=3,
            prefer_implementation=True,
            priority_paths=["webfw/db/models/query_utils.py"],
        )

        assert capped is True
        assert "viking://r/webfw/db/models/query_utils.py" in uris

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
class DiagnosticDomain:
    def get_resource_id(self):
        return ()

    def _resolve_docref(self):
        logger.warning("resource id is missing for %s: %s")
"""
        pdf_content = """\
class Table:
    def get_table_type(self): pass
    def visit_table(self): pass
    def depart_table(self): pass
import warnings
# pdf table singlepage docref table pdf
"""

        result = search_code(
            "get_resource_id table resource id is missing docref warning table singlepage pdf",
            [
                (pdf_content, "viking://r/docsuite/writers/pdf.py"),
                (std_content, "viking://r/docsuite/domains/std.py"),
            ],
        )

        assert "Diagnostic search note" in result
        assert "path-only matches as context" in result
        assert result.index("viking://r/docsuite/domains/std.py") < result.index(
            "viking://r/docsuite/writers/pdf.py"
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
    def test_locate_code_defaults_to_compact_candidates_and_guidance(self):
        files = [
            CodeLocateFile(
                f"def target_{idx}():\n    return 'fix parser regression {idx}'\n",
                f"/repo/pkg/module_{idx}.py",
                location_type="local",
                relative_path=f"pkg/module_{idx}.py",
            )
            for idx in range(5)
        ] + [
            CodeLocateFile(
                f"def test_target_{idx}():\n    assert 'parser regression'\n",
                f"/repo/tests/test_module_{idx}.py",
                location_type="local",
                relative_path=f"tests/test_module_{idx}.py",
            )
            for idx in range(3)
        ]

        result = locate_code_structured("fix parser regression target", files)
        text = format_locate_text(result)

        assert len(result.edit_candidates) <= 3
        assert len(result.behavior_references) <= 2
        assert "Contract: follow each candidate confidence and next action" in text
        assert (
            "Patch before broader grep/read/codesearch only for high-confidence"
            in text
        )
        assert "If pytest fails before collection or dependency imports" in text

    def test_locate_code_structured_uses_structured_terms_and_hints(self):
        result = locate_code_structured(
            "Regression in flag serialization output.",
            [
                CodeLocateFile(
                    content=(
                        "def unrelated():\n"
                        "    return 'flag serialization output regression'\n"
                    ),
                    file_name="/repo/pkg/noise.py",
                    location_type="local",
                    relative_path="pkg/noise.py",
                ),
                CodeLocateFile(
                    content=(
                        "def serialize_flag(value):\n"
                        "    raise TypeError('unsupported flag value')\n"
                    ),
                    file_name="/repo/pkg/serializers.py",
                    location_type="local",
                    relative_path="pkg/serializers.py",
                ),
            ],
            terms=["flag", "serializer"],
            hints=CodeLocateHints(
                paths=["serializers.py"],
                symbols=["serialize_flag"],
                errors=["unsupported flag value"],
            ),
            debug=True,
        )

        assert result.edit_candidates[0].location["relative_path"] == "pkg/serializers.py"
        assert any("hint path" in reason for reason in result.edit_candidates[0].reasons)
        assert result.debug["terms"] == ["flag", "serializer"]
        assert result.debug["hints"]["paths"] == ["serializers.py"]

    def test_locate_code_structured_does_not_split_structured_symbol_hints(self):
        result = locate_code_structured(
            "cluster metrics validation",
            [
                CodeLocateFile(
                    "def check_estimators_nan_inf():\n    return True\n",
                    "/repo/samplepkg/utils/estimator_checks.py",
                    location_type="local",
                    relative_path="samplepkg/utils/estimator_checks.py",
                ),
                CodeLocateFile(
                    "def check_clusterings(labels_true, labels_pred):\n    return labels_true\n",
                    "/repo/samplepkg/metrics/cluster/_supervised.py",
                    location_type="local",
                    relative_path="samplepkg/metrics/cluster/_supervised.py",
                ),
            ],
            hints=CodeLocateHints(symbols=["check_clusterings"]),
        )

        by_path = {
            candidate.location["relative_path"]: candidate
            for candidate in result.edit_candidates
        }
        assert "hint symbols: check_clusterings" in by_path[
            "samplepkg/metrics/cluster/_supervised.py"
        ].reasons
        assert not any(
            reason.startswith("hint symbols")
            for reason in by_path["samplepkg/utils/estimator_checks.py"].reasons
        )

    def test_locate_code_structured_boosts_generic_operation_family_symbols(self):
        result = locate_code_structured(
            "Squash duplicate operations when merging migration-style edits.",
            [
                CodeLocateFile(
                    (
                        "def describe_issue():\n"
                        "    return 'squash duplicate operations merging edits'\n"
                    ),
                    "/repo/pkg/reporting.py",
                    location_type="local",
                    relative_path="pkg/reporting.py",
                ),
                CodeLocateFile(
                    "def combine_operations(operations):\n    return operations\n",
                    "/repo/pkg/optimizer.py",
                    location_type="local",
                    relative_path="pkg/optimizer.py",
                ),
            ],
        )

        assert result.edit_candidates[0].location["relative_path"] == "pkg/optimizer.py"
        assert any(
            "operation-family symbol" in reason
            for reason in result.edit_candidates[0].reasons
        )

    def test_locate_code_honors_explicit_path_hint_over_generic_content_noise(self):
        result = locate_code_structured(
            "Where ChangePlanner orders operations for create model and altered options including relative_order_field and indexes",
            [
                CodeLocateFile(
                    (
                        "class GenericRelation:\n"
                        "    def contribute_to_class(self, model):\n"
                        "        if model._meta.relative_order_field:\n"
                        "            return 'create operation order indexes fields'\n"
                    ),
                    "/repo/webfw/contrib/contenttypes/fields.py",
                    location_type="local",
                    relative_path="webfw/contrib/contenttypes/fields.py",
                ),
                CodeLocateFile(
                    (
                        "class ChangePlanner:\n"
                        "    def generate_created_models(self):\n"
                        "        self.add_operation('AlterRelativeOrderField')\n"
                        "    def generate_altered_options(self):\n"
                        "        return 'indexes relative_order_field'\n"
                    ),
                    "/repo/webfw/db/migrations/autodetector.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/autodetector.py",
                ),
            ],
            terms=[
                "relative_order_field",
                "AddIndex",
                "AlterRelativeOrderField",
                "ChangePlanner",
                "generate_created_models",
                "add_operation",
                "indexes",
            ],
            hints=CodeLocateHints(
                paths=["webfw/db/migrations/autodetector.py"],
                path_terms=["autodetector"],
                symbols=[
                    "ChangePlanner",
                    "generate_created_models",
                    "generate_altered_options",
                ],
                imports=["webfw.db.migrations.autodetector"],
            ),
            failing_tests=["test_order_fields_indexes"],
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "webfw/db/migrations/autodetector.py"
        )

    def test_locate_code_honors_specific_path_term_basename_over_symbol_pileup(self):
        result = locate_code_structured(
            "ChangePlanner emits AddIndex before AlterRelativeOrderField for _order",
            [
                CodeLocateFile(
                    (
                        "class AddIndex:\n"
                        "    pass\n"
                        "class AlterRelativeOrderField:\n"
                        "    def database_forwards(self):\n"
                        "        return '_order'\n"
                        "class CreateResource:\n"
                        "    pass\n"
                    ),
                    "/repo/webfw/db/migrations/operations/models.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/operations/models.py",
                ),
                CodeLocateFile(
                    (
                        "class ChangePlanner:\n"
                        "    def generate_altered_relative_order_field(self):\n"
                        "        return 'AlterRelativeOrderField before indexes'\n"
                    ),
                    "/repo/webfw/db/migrations/autodetector.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/autodetector.py",
                ),
            ],
            terms=[
                "ChangePlanner",
                "AddIndex",
                "AlterRelativeOrderField",
                "_order",
            ],
            hints=CodeLocateHints(
                paths=["webfw/db/migrations"],
                path_terms=["migrations", "autodetector", "operations"],
                symbols=[
                    "AlterRelativeOrderField",
                    "AddIndex",
                    "ChangePlanner",
                ],
                imports=["webfw.db.migrations"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "webfw/db/migrations/autodetector.py"
        )

    def test_locate_code_prefers_symbol_definition_over_import_reexport(self):
        result = locate_code_structured(
            "TypeError cannot pickle when applying | operator to a Q object with dict_keys in Q._combine",
            [
                CodeLocateFile(
                    (
                        "from webfw.db.models.query_utils import Q\n\n"
                        "class QuerySet:\n"
                        "    def __or__(self, other):\n"
                        "        return Q() | Q(other)\n"
                    ),
                    "/repo/webfw/db/models/query.py",
                    location_type="local",
                    relative_path="webfw/db/models/query.py",
                ),
                CodeLocateFile(
                    (
                        "class Q:\n"
                        "    def _combine(self, other, conn):\n"
                        "        obj = self.copy()\n"
                        "        return obj\n"
                        "    def __or__(self, other):\n"
                        "        return self._combine(other, 'OR')\n"
                    ),
                    "/repo/webfw/db/models/query_utils.py",
                    location_type="local",
                    relative_path="webfw/db/models/query_utils.py",
                ),
            ],
            terms=["cannot pickle", "dict_keys", "Q", "_combine", "__or__"],
            hints=CodeLocateHints(
                path_terms=["webfw/db/models"],
                symbols=["Q", "_combine", "__or__"],
                imports=["webfw.db.models.Q"],
                errors=["TypeError: cannot pickle 'dict_keys' object"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "webfw/db/models/query_utils.py"
        )

    def test_warning_query_without_emitter_keeps_normal_guidance(self):
        result = locate_code_structured(
            "Squashing migrations with legacy_indexes should remove deprecation warnings",
            [
                CodeLocateFile(
                    (
                        "class CreateResource:\n"
                        "    def reduce(self, operation):\n"
                        "        return 'legacy_indexes indexes squashed migration'\n"
                    ),
                    "/repo/webfw/db/migrations/operations/models.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/operations/models.py",
                ),
                CodeLocateFile(
                    (
                        "class Command:\n"
                        "    help = 'squash migrations'\n"
                        "    def handle(self):\n"
                        "        return 'migration squash command'\n"
                    ),
                    "/repo/webfw/core/management/commands/squashmigrations.py",
                    location_type="local",
                    relative_path="webfw/core/management/commands/squashmigrations.py",
                ),
            ],
            terms=["legacy_indexes", "indexes", "squash", "deprecation warning"],
            hints=CodeLocateHints(
                path_terms=["migrations", "squash"],
                symbols=["CreateResource", "AlterLegacyIndexes"],
                imports=["webfw.db.migrations"],
                errors=["legacy_indexes deprecation warning"],
            ),
        )

        next_action = result.edit_candidates[0].next_action
        assert "patch before broader grep/read/codesearch" in next_action
        assert "diagnostic emitter" not in next_action
        assert "message/arguments" not in next_action

    def test_locate_code_does_not_promote_deprecation_helper_by_basename(self):
        result = locate_code_structured(
            "Squashed migration should not preserve deprecated legacy_indexes options",
            [
                CodeLocateFile(
                    (
                        "class RemovedInWebfw51Warning(DeprecationWarning):\n"
                        "    pass\n"
                    ),
                    "/repo/webfw/utils/deprecation.py",
                    location_type="local",
                    relative_path="webfw/utils/deprecation.py",
                ),
                CodeLocateFile(
                    (
                        "class CreateResource:\n"
                        "    def reduce(self, operation):\n"
                        "        return 'legacy_indexes indexes squashed migration'\n"
                    ),
                    "/repo/webfw/db/migrations/operations/models.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/operations/models.py",
                ),
            ],
            terms=["legacy_indexes", "indexes", "squash", "deprecation warning"],
            hints=CodeLocateHints(
                path_terms=["migrations", "squash", "deprecation", "legacy_indexes"],
                symbols=["CreateResource", "AlterLegacyIndexes"],
                imports=["webfw.db.migrations"],
                errors=["legacy_indexes is deprecated"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "webfw/db/migrations/operations/models.py"
        )

    def test_runtime_error_query_without_diagnostic_emitter_uses_normal_next_action(self):
        result = locate_code_structured(
            "mutual_info_score raises ValueError when object string labels are validated",
            [
                CodeLocateFile(
                    (
                        "def check_clusterings(labels_true, labels_pred):\n"
                        "    labels_true = check_array(labels_true, ensure_2d=False)\n"
                        "    labels_pred = check_array(labels_pred, ensure_2d=False)\n"
                        "    return labels_true, labels_pred\n"
                    ),
                    "/repo/samplepkg/metrics/cluster/_supervised.py",
                    location_type="local",
                    relative_path="samplepkg/metrics/cluster/_supervised.py",
                ),
                CodeLocateFile(
                    "def test_mutual_info_score():\n    assert mutual_info_score(['a'], ['a']) == 1\n",
                    "/repo/samplepkg/metrics/cluster/tests/test_supervised.py",
                    location_type="local",
                    relative_path="samplepkg/metrics/cluster/tests/test_supervised.py",
                ),
            ],
            terms=["mutual_info_score", "ValueError", "object labels"],
            hints=CodeLocateHints(
                paths=["samplepkg/metrics/cluster"],
                symbols=["mutual_info_score", "check_clusterings", "check_array"],
            ),
        )

        next_action = result.edit_candidates[0].next_action
        assert "patch before broader grep/read/codesearch" in next_action
        assert "diagnostic emitter" not in next_action
        assert "message/arguments" not in next_action

    def test_lower_ranked_diagnostic_hit_does_not_switch_top_edit_guidance(self):
        result = locate_code_structured(
            "warning appears during migration optimization for legacy_indexes",
            [
                CodeLocateFile(
                    "class ChangePlanner:\n    def optimize(self):\n        return 'legacy_indexes indexes migration'\n",
                    "/repo/webfw/db/migrations/autodetector.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/autodetector.py",
                ),
                CodeLocateFile(
                    "def unrelated():\n    warnings.warn('legacy_indexes warning')\n",
                    "/repo/webfw/contrib/postgres/indexes.py",
                    location_type="local",
                    relative_path="webfw/contrib/postgres/indexes.py",
                ),
                CodeLocateFile(
                    "def test_migration_warning():\n    assert 'legacy_indexes' in output\n",
                    "/repo/tests/migrations/test_autodetector.py",
                    location_type="local",
                    relative_path="tests/migrations/test_autodetector.py",
                ),
            ],
            terms=["legacy_indexes", "indexes", "migration"],
            hints=CodeLocateHints(
                paths=["webfw/db/migrations/autodetector.py"],
                path_terms=["migrations"],
                imports=["webfw.db.migrations"],
                symbols=["ChangePlanner"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "webfw/db/migrations/autodetector.py"
        )
        next_action = result.edit_candidates[0].next_action
        assert "patch before broader grep/read/codesearch" in next_action
        assert "diagnostic emitter" not in next_action

    def test_locate_code_structured_downranks_broad_hint_matches(self):
        result = locate_code_structured(
            "ValueError truth value of an array ambiguous compact_repr repr vector values",
            [
                CodeLocateFile(
                    (
                        "import numpy as np\n\n"
                        "class Kernel:\n"
                        "    def __repr__(self):\n"
                        "        return repr(np.array([]))\n\n"
                        "class Sum:\n"
                        "    def __repr__(self):\n"
                        "        return 'value changed only repr'\n\n"
                        "class Product:\n"
                        "    def __repr__(self):\n"
                        "        return 'value changed only repr'\n"
                    ),
                    "/repo/samplepkg/gaussian_process/kernels.py",
                    location_type="local",
                    relative_path="samplepkg/gaussian_process/kernels.py",
                ),
                CodeLocateFile(
                    (
                        "import numpy as np\n\n"
                        "def _changed_params(estimator):\n"
                        "    params = estimator.get_params(deep=False)\n"
                        "    filtered_params = {}\n"
                        "    for k, v in params.items():\n"
                        "        if v != init_params[k]:\n"
                        "            filtered_params[k] = v\n"
                        "    return filtered_params\n\n"
                        "class _EstimatorPrettyPrinter:\n"
                        "    def __init__(self):\n"
                        "        self._changed_only = get_config()['compact_repr']\n"
                        "    def __repr__(self):\n"
                        "        return 'repr'\n"
                    ),
                    "/repo/samplepkg/utils/pretty.py",
                    location_type="local",
                    relative_path="samplepkg/utils/pretty.py",
                ),
            ],
            terms=["compact_repr", "repr"],
            hints=CodeLocateHints(
                paths=["samplepkg"],
                path_terms=["base", "utils", "repr"],
                symbols=["__repr__"],
                imports=["numpy"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == "samplepkg/utils/pretty.py"

    def test_locate_code_marks_weak_hint_only_candidate_low_confidence(self):
        result = locate_code_structured(
            "fix request routing regression",
            [
                CodeLocateFile(
                    "def helper():\n    return 'shared utility'\n",
                    "/repo/webfw/core/router.py",
                    location_type="local",
                    relative_path="webfw/core/router.py",
                ),
                CodeLocateFile(
                    "def parse_request():\n    return 'request handling route local evidence'\n",
                    "/repo/webfw/http/request.py",
                    location_type="local",
                    relative_path="webfw/http/request.py",
                ),
            ],
            hints=CodeLocateHints(paths=["webfw/core/router.py"]),
        )

        candidate = result.edit_candidates[0]
        assert candidate.confidence in {"low", "medium"}
        assert "patch before broader grep/read/codesearch" not in candidate.next_action
        assert "read this top edit file first" not in candidate.next_action

    def test_locate_code_prefers_local_test_colocation_over_broad_hints(self):
        result = locate_code_structured(
            "ColumnTransformer fails to preserve pandas feature names in output",
            [
                CodeLocateFile(
                    "class Pipeline:\n    def transform(self, X):\n        return X\n",
                    "/repo/sklearn/pipeline.py",
                    location_type="local",
                    relative_path="sklearn/pipeline.py",
                ),
                CodeLocateFile(
                    (
                        "class ColumnTransformer:\n"
                        "    def get_feature_names_out(self):\n"
                        "        return self.transformers_  # pandas feature names output\n"
                    ),
                    "/repo/sklearn/compose/_column_transformer.py",
                    location_type="local",
                    relative_path="sklearn/compose/_column_transformer.py",
                ),
                CodeLocateFile(
                    (
                        "def test_column_transformer_pandas_feature_names():\n"
                        "    assert ColumnTransformer().get_feature_names_out()\n"
                    ),
                    "/repo/sklearn/compose/tests/test_column_transformer.py",
                    location_type="local",
                    relative_path="sklearn/compose/tests/test_column_transformer.py",
                ),
            ],
            terms=["ColumnTransformer", "feature_names_out", "pandas"],
            hints=CodeLocateHints(path_terms=["pipeline"], symbols=["Pipeline"]),
            failing_tests=[
                "sklearn/compose/tests/test_column_transformer.py::test_column_transformer_pandas_feature_names",
            ],
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "sklearn/compose/_column_transformer.py"
        )
        assert result.behavior_references[0].location["relative_path"] == (
            "sklearn/compose/tests/test_column_transformer.py"
        )

    def test_locate_code_avoids_concrete_pytest_for_weak_source_test_pair(self):
        result = locate_code_structured(
            "request routing regression",
            [
                CodeLocateFile(
                    "def request(path):\n    return path\n",
                    "/repo/webfw/http/request.py",
                    location_type="local",
                    relative_path="webfw/http/request.py",
                ),
                CodeLocateFile(
                    "def test_auth_permissions():\n    request = object()\n    assert request\n",
                    "/repo/tests/auth/test_permissions.py",
                    location_type="local",
                    relative_path="tests/auth/test_permissions.py",
                ),
            ],
            source_root="/repo",
        )

        commands = [item["command"] for item in result.verification if item.get("command")]
        assert "python3 -m py_compile webfw/http/request.py" in commands
        assert "python3 -m pytest tests/auth/test_permissions.py" not in commands

    def test_locate_code_does_not_pair_tests_on_short_substrings(self):
        result = locate_code_structured(
            "io parsing regression",
            [
                CodeLocateFile(
                    "def load_io(stream):\n    return stream.read()\n",
                    "/repo/pkg/io.py",
                    location_type="local",
                    relative_path="pkg/io.py",
                ),
                CodeLocateFile(
                    "def test_condition_message():\n    assert 'condition' in message\n",
                    "/repo/tests/test_messages.py",
                    location_type="local",
                    relative_path="tests/test_messages.py",
                ),
            ],
            source_root="/repo",
        )

        commands = [item["command"] for item in result.verification if item.get("command")]
        assert "python3 -m py_compile pkg/io.py" in commands
        assert "python3 -m pytest tests/test_messages.py" not in commands

    def test_locate_code_ignores_package_name_path_hint_as_broad_noise(self):
        result = locate_code_structured(
            "ValueError truth value of an array ambiguous compact_repr repr",
            [
                CodeLocateFile(
                    "class BaseEstimator:\n    def _get_param_names(cls):\n        return []\n",
                    "/repo/samplepkg/base.py",
                    location_type="local",
                    relative_path="samplepkg/base.py",
                ),
                CodeLocateFile(
                    (
                        "def _changed_params(estimator):\n"
                        "    compact_repr = True\n"
                        "    if value != init_params[name]:\n"
                        "        return repr(value)\n"
                        "        return value\n"
                    ),
                    "/repo/samplepkg/utils/pretty.py",
                    location_type="local",
                    relative_path="samplepkg/utils/pretty.py",
                ),
            ],
            terms=["compact_repr", "repr"],
            hints=CodeLocateHints(
                paths=["samplepkg"],
                path_terms=["base", "utils"],
                symbols=["_get_param_names"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == "samplepkg/utils/pretty.py"

    def test_locate_code_ignores_natural_language_failing_test_as_path_hint(self):
        result = locate_code_structured(
            "ValueError truth value of an array ambiguous compact_repr repr vector values",
            [
                CodeLocateFile(
                    "class DictVectorizer:\n    '''Transforms mappings to vectors.'''\n",
                    "/repo/samplepkg/feature_extraction/dict_vectorizer.py",
                    location_type="local",
                    relative_path="samplepkg/feature_extraction/dict_vectorizer.py",
                ),
                CodeLocateFile(
                    (
                        "def _changed_params(estimator):\n"
                        "    if value != init_params[name]:\n"
                        "        return value\n"
                        "class _EstimatorPrettyPrinter:\n"
                        "    pass\n"
                    ),
                    "/repo/samplepkg/utils/pretty.py",
                    location_type="local",
                    relative_path="samplepkg/utils/pretty.py",
                ),
            ],
            terms=["compact_repr", "repr"],
            failing_tests=["repr with vector values"],
        )

        assert result.edit_candidates[0].location["relative_path"] == "samplepkg/utils/pretty.py"

    def test_locate_code_prefers_operation_family_symbol_for_squash_issues(self):
        result = locate_code_structured(
            "Squashing migrations should fold old options into the final output.",
            [
                CodeLocateFile(
                    "class Options:\n    legacy_indexes = None\n",
                    "/repo/webfw/db/models/options.py",
                    location_type="local",
                    relative_path="webfw/db/models/options.py",
                ),
                CodeLocateFile(
                    (
                        "class CreateResource:\n"
                        "    def reduce(self, operation, app_label):\n"
                        "        return 'fold options into migration operation'\n"
                    ),
                    "/repo/webfw/db/migrations/operations/models.py",
                    location_type="local",
                    relative_path="webfw/db/migrations/operations/models.py",
                ),
            ],
            terms=["squash", "options"],
            hints=CodeLocateHints(
                paths=["webfw/db/migrations", "webfw/db/models/options.py"],
                symbols=["Options"],
            ),
        )

        assert result.edit_candidates[0].location["relative_path"] == (
            "webfw/db/migrations/operations/models.py"
        )

    def test_locate_code_structured_prefers_package_code_over_examples(self):
        result = locate_code_structured(
            "Regression in input validation of clustering metrics",
            [
                CodeLocateFile(
                    (
                        "from samplepkg.metrics.cluster import mutual_info_score\n\n"
                        "def plot_agglomerative_clustering_metrics():\n"
                        "    return mutual_info_score(['a'], ['a'])\n"
                    ),
                    "/repo/examples/cluster/plot_agglomerative_clustering_metrics.py",
                    location_type="local",
                    relative_path="examples/cluster/plot_agglomerative_clustering_metrics.py",
                ),
                CodeLocateFile(
                    (
                        "def check_clusterings(labels_true, labels_pred):\n"
                        "    labels_true = check_array(labels_true, ensure_2d=False)\n"
                        "    labels_pred = check_array(labels_pred, ensure_2d=False)\n"
                        "    return labels_true, labels_pred\n\n"
                        "def mutual_info_score(labels_true, labels_pred):\n"
                        "    labels_true, labels_pred = check_clusterings(labels_true, labels_pred)\n"
                        "    return 1.0\n"
                    ),
                    "/repo/samplepkg/metrics/cluster/_supervised.py",
                    location_type="local",
                    relative_path="samplepkg/metrics/cluster/_supervised.py",
                ),
            ],
            terms=["mutual_info_score", "input validation", "clustering metrics"],
            hints=CodeLocateHints(
                paths=["samplepkg/metrics/cluster"],
                symbols=["check_clusterings", "mutual_info_score"],
                imports=["samplepkg.metrics.cluster"],
            ),
        )

        assert (
            result.edit_candidates[0].location["relative_path"]
            == "samplepkg/metrics/cluster/_supervised.py"
        )

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

        assert "Likely edit locations:" in result
        assert "Useful behavior references:" in result
        assert "viking://r/pylint/checkers/misc.py" in result
        assert "viking://r/tests/checkers/unittest_misc.py" in result
        assert result.index("viking://r/pylint/checkers/misc.py") < result.index(
            "Useful behavior references:"
        )
        assert result.index("Useful behavior references:") < result.index(
            "viking://r/tests/checkers/unittest_misc.py"
        )
        assert "Contract: follow each candidate confidence and next action" in result
        assert "next: read this top edit file first" in result
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
                '"""This module contains methods in the samplepkg package."""',
                "# in the an is with samplepkg",
                "# in the an is with samplepkg",
                "# in the an is with samplepkg",
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
    def pretty_estimator(self, object, stream, indent, allowance, context, level):
        return object.__repr__()
'''

        result = locate_code(
            "bug in compact_repr in new repr: vector values truth value array ambiguous",
            [
                (noisy_content, "viking://r/samplepkg/ensemble/gradient_boosting.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
            ],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/ensemble/gradient_boosting.py"
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
    def pretty_estimator(self, object, stream, indent, allowance, context, level):
        return object.__repr__()
'''

        result = locate_code(
            "bug in compact_repr in new repr: vector values truth value array ambiguous",
            [
                (broad_model_file, "viking://r/samplepkg/linear_model/logistic.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
            ],
            failing_tests=["samplepkg/utils/tests/test_pretty.py::test_changed_only"],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/linear_model/logistic.py"
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
        self._changed_only = get_config()["compact_repr"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            "bug in compact_repr repr generation for vector-valued params",
            [
                (vectorizer_content, "viking://r/samplepkg/feature_extraction/dict_vectorizer.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
            ],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/feature_extraction/dict_vectorizer.py"
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
        self._changed_only = get_config()["compact_repr"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            "bug in compact_repr in new repr: vector values. "
            "Reproduces with LogisticRegressionCV(Cs=np.array([0.1, 1])) after "
            "samplepkg.set_config(compact_repr=True): ValueError from ambiguous "
            "truth value of array. Need fix repr generation for vector/array-valued "
            "params in compact_repr mode.",
            [
                (vectorizer_content, "viking://r/samplepkg/feature_extraction/dict_vectorizer.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
            ],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/feature_extraction/dict_vectorizer.py"
        )

    def test_locate_code_ignores_reproducing_setup_for_exact_identifiers(self):
        config_content = '''"""Global configuration."""

_global_config = {"compact_repr": False}


def get_config():
    return _global_config.copy()


def set_config(compact_repr=None):
    if compact_repr is not None:
        _global_config["compact_repr"] = compact_repr
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
        self._changed_only = get_config()["compact_repr"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            "bug in compact_repr repr logic for vector-valued parameters. "
            "Reproducing with LogisticRegressionCV(Cs=np.array([0.1, 1])) under "
            "samplepkg.set_config(compact_repr=True) raises ValueError.",
            [
                (config_content, "viking://r/samplepkg/_config.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
            ],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/_config.py"
        )

    def test_locate_code_downranks_fenced_reproduction_code(self):
        config_content = '''"""Global configuration."""

_global_config = {"compact_repr": False}


def get_config():
    return _global_config.copy()


def set_config(compact_repr=None):
    if compact_repr is not None:
        _global_config["compact_repr"] = compact_repr
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
        self._changed_only = get_config()["compact_repr"]

    def _safe_repr(self, object, context, maxlevels, level):
        return repr(object)
'''

        result = locate_code(
            """bug in compact_repr in new repr: vector values
```python
import samplepkg
import numpy as np
from samplepkg.linear_model import LogisticRegressionCV
samplepkg.set_config(compact_repr=True)
print(LogisticRegressionCV(Cs=np.array([0.1, 1])))
```
> ValueError: The truth value of an array with more than one element is ambiguous.
""",
            [
                (config_content, "viking://r/samplepkg/_config.py"),
                (logistic_content, "viking://r/samplepkg/linear_model/logistic.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
            ],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/_config.py"
        )
        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/linear_model/logistic.py"
        )

    def test_locate_code_downranks_agent_setup_context(self):
        config_content = '''"""Global configuration."""

_global_config = {"compact_repr": False}


def get_config():
    return _global_config.copy()


def set_config(compact_repr=None):
    if compact_repr is not None:
        _global_config["compact_repr"] = compact_repr
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
        self._changed_only = get_config()["compact_repr"]

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
from samplepkg import get_config, set_config, config_context


def test_set_config():
    set_config(compact_repr=True)
    assert get_config()["compact_repr"]
"""
        test_pretty_content = """\
import numpy as np
from samplepkg import set_config
from samplepkg.linear_model import LogisticRegressionCV


def test_changed_only():
    set_config(compact_repr=True)
    repr(LogisticRegressionCV(Cs=np.array([0.1, 1])))
"""

        result = locate_code(
            "Bug in `compact_repr` repr for estimators with vector-valued "
            "parameters: `LogisticRegressionCV(Cs=np.array([0.1, 1]))` raises "
            "`ValueError: The truth value of an array with more than one element "
            "is ambiguous` when `samplepkg.set_config(compact_repr=True)` is "
            "enabled. Find the repr/pretty-print code path and likely edit locations.",
            [
                (config_content, "viking://r/samplepkg/_config.py"),
                (estimator_checks_content, "viking://r/samplepkg/utils/estimator_checks.py"),
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
                (test_config_content, "viking://r/samplepkg/tests/test_config.py"),
                (test_pretty_content, "viking://r/samplepkg/utils/tests/test_pretty.py"),
            ],
        )

        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/_config.py"
        )
        assert result.index("viking://r/samplepkg/utils/pretty.py") < result.index(
            "viking://r/samplepkg/utils/estimator_checks.py"
        )
        assert result.index("viking://r/samplepkg/utils/tests/test_pretty.py") < result.index(
            "viking://r/samplepkg/tests/test_config.py"
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
        test_pretty_content = """\
import numpy as np
from samplepkg.linear_model import LogisticRegressionCV


def test_changed_only():
    repr(LogisticRegressionCV(Cs=np.array([0.1, 1])))
"""

        result = locate_code(
            "Bug in compact_repr repr for vector-valued estimator params",
            [
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
                (test_pretty_content, "viking://r/samplepkg/utils/tests/test_pretty.py"),
            ],
        )

        assert "Suggested verification:" in result
        assert "python3 -m py_compile samplepkg/utils/pretty.py" in result
        assert "python3 -m pytest samplepkg/utils/tests/test_pretty.py" in result
        assert "If pytest fails before collection or dependency imports" in result
        assert "do not broaden code search" in result

    def test_locate_code_suggested_verification_stays_minimal(self):
        pprint_content = """\
from samplepkg._config import get_config


def _changed_params(estimator):
    if get_config()["compact_repr"]:
        return repr(estimator)
    return {}
"""
        example_content = """\
from samplepkg import set_config


set_config(compact_repr=True)
print("changed only repr example")
"""
        test_pretty_content = """\
from samplepkg import set_config


def test_changed_only():
    set_config(compact_repr=True)
"""
        test_config_content = """\
from samplepkg import get_config, set_config


def test_config_context():
    set_config(compact_repr=True)
    assert get_config()["compact_repr"]
"""

        result = locate_code(
            "Bug in compact_repr repr for vector-valued estimator params",
            [
                (pprint_content, "viking://r/samplepkg/utils/pretty.py"),
                (example_content, "viking://r/examples/plot_changed_onlypretty_parameter.py"),
                (test_pretty_content, "viking://r/samplepkg/utils/tests/test_pretty.py"),
                (test_config_content, "viking://r/samplepkg/tests/test_config.py"),
            ],
        )

        static_line = next(line for line in result.splitlines() if line.startswith("- static:"))
        tests_line = next(line for line in result.splitlines() if line.startswith("- narrow tests:"))
        assert static_line == "- static: python3 -m py_compile samplepkg/utils/pretty.py"
        assert tests_line == "- narrow tests: python3 -m pytest samplepkg/utils/tests/test_pretty.py"

    def test_locate_code_structured_quotes_verification_paths(self):
        result = locate_code_structured(
            "Bug in print changed only repr",
            [
                CodeLocateFile(
                    "def _changed_params(estimator):\n    return repr(estimator)\n",
                    "/repo/pkg with spaces/pretty.py",
                    location_type="local",
                    relative_path="pkg with spaces/pretty.py",
                ),
                CodeLocateFile(
                    "def test_changed_only():\n    assert repr(estimator)\n",
                    "/repo/pkg with spaces/tests/test_pretty.py",
                    location_type="local",
                    relative_path="pkg with spaces/tests/test_pretty.py",
                ),
            ],
            source_root="/repo",
        )

        commands = [item["command"] for item in result.verification if item.get("command")]
        assert "python3 -m py_compile 'pkg with spaces/pretty.py'" in commands
        assert "python3 -m pytest 'pkg with spaces/tests/test_pretty.py'" in commands

    def test_locate_code_boosts_nearby_issue_terms_over_repeated_noise(self):
        noisy_order_content = "\n".join(
            [
                "class ListView:",
                "    def get_ordering(self):",
            ]
            + ["        return self.ordering  # order value" for _ in range(40)]
        )
        migration_content = """\
from webfw.db.migrations import operations


class ChangePlanner:
    def generate_created_models(self):
        operations.CreateResource(name="LookImage")
        operations.AddIndex(
            model_name="lookimage",
            index=models.Index(fields=["look", "_order"]),
        )
        operations.AlterRelativeOrderField(
            name="lookimage",
            relative_order_field="look",
        )
"""

        result = locate_code(
            "AlterRelativeOrderField with ForeignKey crash when _order is included "
            "in AddIndex. AddIndex of _order is emitted before "
            "AlterRelativeOrderField creates the _order field.",
            [
                (noisy_order_content, "viking://r/webfw/views/generic/list.py"),
                (migration_content, "viking://r/webfw/db/migrations/autodetector.py"),
            ],
        )

        assert result.index("viking://r/webfw/db/migrations/autodetector.py") < result.index(
            "viking://r/webfw/views/generic/list.py"
        )

    def test_locate_code_verification_guidance_bounds_agent_search(self):
        result = locate_code(
            "WARNING resource id is missing for table docref in singlepage pdf",
            [
                (
                    "def assign_figure_numbers(app):\n"
                    "    return {'table': app.config.resource_ids}\n",
                    "viking://r/docsuite/environment/collectors/toctree.py",
                ),
                (
                    "def test_docref_table_warning(app):\n"
                    "    assert 'table' in app.builder.resource_ids\n",
                    "viking://r/tests/test_build_html.py",
                ),
            ],
        )

        assert "Run the static check first" in result
        assert "do not broaden code search" in result
        assert "Do not use web, upstream patches, or git log" in result

    def test_locate_code_structured_verification_guidance_bounds_agent_search(self):
        result = locate_code_structured(
            "WARNING resource id is missing for table docref in singlepage pdf",
            [
                CodeLocateFile(
                    content=(
                        "def assign_figure_numbers(app):\n"
                        "    return {'table': app.config.resource_ids}\n"
                    ),
                    file_name="/repo/docsuite/environment/collectors/toctree.py",
                    location_type="local",
                    relative_path="docsuite/environment/collectors/toctree.py",
                ),
                CodeLocateFile(
                    content=(
                        "def test_docref_table_warning(app):\n"
                        "    assert 'table' in app.builder.resource_ids\n"
                    ),
                    file_name="/repo/tests/test_build_html.py",
                    location_type="local",
                    relative_path="tests/test_build_html.py",
                ),
            ],
            source_root="/repo",
        )

        setup_note = next(item for item in result.verification if item["kind"] == "setup_note")
        assert "Run the static check first" in setup_note["reason"]
        assert "do not broaden code search" in setup_note["reason"]
        assert "Do not use web, upstream patches, or git log" in setup_note["reason"]

    def test_locate_code_structured_uses_existing_fields_for_agent_harness_guidance(self):
        result = locate_code_structured(
            "WARNING resource id is missing for table docref in singlepage pdf",
            [
                CodeLocateFile(
                    content=(
                        "class DiagnosticDomain:\n"
                        "    def _resolve_docref(self, env, fromdocname, builder):\n"
                        "        return env.toc_resource_ids.get('table')\n"
                    ),
                    file_name="/repo/docsuite/domains/std.py",
                    location_type="local",
                    relative_path="docsuite/domains/std.py",
                ),
                CodeLocateFile(
                    content=(
                        "def test_docref_table_warning(app):\n"
                        "    assert 'table' in app.builder.resource_ids\n"
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
        assert result.edit_candidates[0].confidence == "medium"
        assert "top behavior reference or one alternate before patching" in (
            result.edit_candidates[0].next_action
        )
        assert "patch before broader grep/read/codesearch" not in (
            result.edit_candidates[0].next_action
        )
        assert "lookup" not in result.edit_candidates[0].next_action
        assert "numbering rewrites" not in result.edit_candidates[0].next_action

    def test_locate_code_prioritizes_warning_emitter_and_assertions(self):
        std_content = """\
class DiagnosticDomain:
    def _resolve_docref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            resource_id = self.get_resource_id(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("resource id is missing for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def warn_missing_xref_title(self, target, node):
        msg = __("Failed to resolve a document reference. A title or label was not found: %s")
        logger.warning(msg % target, location=node)
"""
        toctree_content = """\
class TocTreeCollector:
    def assign_figure_numbers(self, env):
        # Skip if uncaptioned node.
        if domain.name == "std" and not domain.get_resource_ids_title(node):
            return None
"""
        broad_writer_content = """\
class PdfTranslator:
    def visit_table(self, node):
        if self.builder.name == "pdf":
            self.body.append("resource_ids table numbering")
        if self.builder.name == "singlepage":
            self.body.append("assigned number changed for table")
        return "table resource_ids numbering assigned number pdf singlepage"
"""
        html_test_content = """\
def test_resource_ids_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings
"""
        pdf_test_content = """\
def test_resource_ids_table_warning_is_not_emitted(app, warning):
    app.build()
    assert "WARNING: resource id is missing for table:" not in warning.getvalue()
"""
        root_conf_content = """\
import docsuite

resource_ids = True
pdf_documents = [("index", "DocSuite.tex", "DocSuite", "DocSuite", "manual")]
keep_warnings = True
"""
        unrelated_test_content = """\
def test_interdocsuite_warning(app, warning):
    assert "WARNING" in warning.getvalue()
"""

        result = locate_code_structured(
            "DocSuite 3.3 upgrade started generating "
            'warning "resource id is missing for table" when building '
            "singlepage or pdf with docref.",
            [
                CodeLocateFile(
                    toctree_content,
                    "viking://r/docsuite/environment/collectors/toctree.py",
                ),
                CodeLocateFile(std_content, "viking://r/docsuite/domains/std.py"),
                CodeLocateFile(broad_writer_content, "viking://r/docsuite/writers/pdf.py"),
                CodeLocateFile(root_conf_content, "viking://r/tests/roots/test-root/conf.py"),
                CodeLocateFile(
                    unrelated_test_content,
                    "viking://r/tests/test_ext_interdocsuite.py",
                ),
                CodeLocateFile(pdf_test_content, "viking://r/tests/test_build_pdf.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        assert result.edit_candidates[0].location["uri"] == "viking://r/docsuite/domains/std.py"
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
            "resource id is missing for section" in snippet["text"]
            for snippet in result.behavior_references[0].snippets
        )
        assert any(
            "same-file diagnostic precedent" in snippet["text"]
            for snippet in result.edit_candidates[0].snippets
        )
        assert any(
            "same-file diagnostic precedent found" in reason
            for reason in result.edit_candidates[0].reasons
        )
        assert "diagnostic wording or argument delta" in result.edit_candidates[0].next_action
        assert "diagnostic wording or argument delta" in result.edit_candidates[0].next_action
        assert "production diagnostic emitter" in result.edit_candidates[0].next_action
        assert "same-file diagnostic precedents as style evidence" in (
            result.edit_candidates[0].next_action
        )
        assert "original semantics" in result.edit_candidates[0].next_action
        assert "immediate verification fails" in result.edit_candidates[0].next_action
        assert "get_resource_id" not in result.edit_candidates[0].next_action
        assert "toc_resource_ids" not in result.edit_candidates[0].next_action
        assert len(result.edit_candidates) == 1
        assert len(result.behavior_references) == 1
        assert result.verification[0]["targets"][0]["relative_path"] == "docsuite/domains/std.py"
        assert all(
            item.get("kind") != "narrow_tests" or item.get("command") is None
            for item in result.verification
        )

    def test_locate_code_prefers_warning_emitter_for_full_docsuite_issue(self):
        std_content = """\
class DiagnosticDomain:
    def _resolve_docref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            resource_id = self.get_resource_id(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("resource id is missing for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def get_resource_id(self, env, builder, figtype, docname, target_node):
        return env.toc_resource_ids[docname][figtype][target_node["ids"][0]]
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
                numbers[figtype] = self.get_resource_ids(env, docname, figtype)
            env.toc_resource_ids[docname] = numbers

    def get_resource_ids(self, env, docname, figtype):
        if figtype == "table":
            return {"id1": (1,)}
        return {}
"""
        html_test_content = """\
def test_resource_ids_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings
"""

        result = locate_code_structured(
            'v3.3 upgrade started generating "WARNING: resource id is missing for table" '
            "warnings. We've updated to DocSuite 3.3 in our documentation, and suddenly "
            "the following warning started popping up in our builds when we build either "
            "`singlepage` or `pdf`: `WARNING: resource id is missing for table:`. "
            "I looked through the changelog but it didn't seem like there was anything "
            "related to `docref` that was changed. Could anyone point me to a change in "
            "the docref logic so I can figure out where these warnings are coming from?",
            [
                CodeLocateFile(toctree_content, "viking://r/docsuite/environment/collectors/toctree.py"),
                CodeLocateFile(std_content, "viking://r/docsuite/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        assert result.edit_candidates[0].location["uri"] == "viking://r/docsuite/domains/std.py"
        assert result.verification[0]["targets"][0]["relative_path"] == "docsuite/domains/std.py"
        assert any(
            "asserted diagnostic wording differs from issue" in reason
            for reason in result.behavior_references[0].reasons
        )

    def test_locate_code_promotes_diagnostic_wording_delta_to_action(self):
        std_content = """\
class DiagnosticDomain:
    def _resolve_docref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            resource_id = self.get_resource_id(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("resource id is missing for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def warn_missing_xref_title(self, target, node):
        msg = __("Failed to resolve a document reference. A title or label was not found: %s")
        logger.warning(msg % target, location=node)
"""
        html_test_content = """\
def test_resource_ids_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings
"""
        singlepage_content = """\
class SingleFileHTMLBuilder:
    name = "singlepage"

    def assemble_toc_resource_ids(self):
        # singlepage pdf table builder numbering docref assigned number
        return {"index": {"table": {"id1": (1,)}}}
"""
        pdf_test_content = """\
def test_docref_with_pdf_builder(app, warning):
    app.build()
    assert "pdf" in app.builder.name
"""

        result = locate_code_structured(
            "DocSuite 3.3 upgrade started generating warning "
            '"resource id is missing for table" for singlepage or pdf builds; '
            "likely docref/table numbering logic regression",
            [
                CodeLocateFile(std_content, "viking://r/docsuite/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
                CodeLocateFile(singlepage_content, "viking://r/docsuite/builders/singlepage.py"),
                CodeLocateFile(pdf_test_content, "viking://r/tests/test_build_pdf.py"),
            ],
            allow_viking_commands=True,
        )

        assert "wording delta" in result.summary_text
        assert "patch diagnostic message/arguments first" in result.summary_text
        assert "as behavior references" in result.summary_text
        assert "python3 -m py_compile docsuite/domains/std.py" in result.summary_text
        assert "python3 -m pytest tests/test_build_html.py" not in result.summary_text
        assert "positive warning assertion means preserve the diagnostic first" in result.summary_text
        assert "same-file diagnostic precedent" in result.summary_text
        assert "style evidence" in result.summary_text
        assert "original semantics" in result.summary_text
        assert "report-only terms are context" in result.summary_text
        assert "run any listed narrow verification after the static check" in result.summary_text
        assert "continue broader discovery only if that immediate verification fails" in result.summary_text
        assert "get_resource_id" not in result.summary_text
        assert "toc_resource_ids" not in result.summary_text
        assert "diagnostic wording or argument delta" in result.edit_candidates[0].next_action
        assert result.edit_candidates[0].next_action.startswith("PATCH FIRST:")
        assert "production diagnostic emitter" in result.edit_candidates[0].next_action
        assert "tests and assertions as behavior evidence" in (
            result.edit_candidates[0].next_action
        )
        assert "diagnostic wording or argument delta" in result.edit_candidates[0].next_action
        assert "same-file diagnostic precedents as style evidence" in (
            result.edit_candidates[0].next_action
        )
        assert "original semantics" in result.edit_candidates[0].next_action
        assert "immediate verification fails" in result.edit_candidates[0].next_action
        assert "get_resource_id" not in result.edit_candidates[0].next_action
        assert "toc_resource_ids" not in result.edit_candidates[0].next_action
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
class DiagnosticDomain:
    def _resolve_docref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            resource_id = self.get_resource_id(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("resource id is missing for %s: %s"), "table", "id1",
                           location=node)
            return contnode
"""
        html_test_content = """\
def test_resource_ids_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings
"""
        pdf_test_content = """\
def test_docref_table_caption(app):
    result = app.outdir.joinpath("python.tex").read_text()
    assert "\\\\docsuitecaption{The table title with a reference" in result
"""

        result = locate_code_structured(
            "DocSuite 3.3 upgrade started generating warning: resource id is missing "
            "for table in singlepage or pdf builds, likely docref logic change",
            [
                CodeLocateFile(std_content, "viking://r/docsuite/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
                CodeLocateFile(pdf_test_content, "viking://r/tests/test_build_pdf.py"),
            ],
            allow_viking_commands=True,
        )

        assert "wording delta" in result.summary_text
        assert len(result.edit_candidates) == 1
        assert result.edit_candidates[0].location["uri"] == "viking://r/docsuite/domains/std.py"
        assert len(result.behavior_references) == 1
        assert (
            result.behavior_references[0].location["uri"]
            == "viking://r/tests/test_build_html.py"
        )

    def test_locate_code_keeps_multiple_positive_diagnostic_assertions(self):
        std_content = """\
class DiagnosticDomain:
    def _resolve_docref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            resource_id = self.get_resource_id(env, builder, "section", "index", node)
        except ValueError:
            logger.warning(__("resource id is missing for %s: %s"), "section", "index",
                           location=node)
            return contnode

    def warn_missing_xref_title(self, target, node):
        msg = __("Failed to resolve a document reference. A title or label was not found: %s")
        logger.warning(msg % target, location=node)
"""
        html_test_content = """\
def test_resource_ids_without_numbered_toctree_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings

def test_resource_ids_with_numbered_toctree_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings

def test_resource_ids_with_prefix_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings

def test_resource_ids_with_secnum_depth_warn(app, warning):
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings
"""

        result = locate_code_structured(
            "DocSuite 3.3 started generating warning: resource id is missing for table "
            "during singlepage or pdf docref builds",
            [
                CodeLocateFile(std_content, "viking://r/docsuite/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        reference = result.behavior_references[0]
        warning_snippets = [
            snippet
            for snippet in reference.snippets
            if "resource id is missing for section" in snippet["text"]
        ]
        assert len(warning_snippets) == 4

    def test_locate_code_keeps_diagnostic_snippets_on_emitter_when_wording_differs(self):
        std_content = """\
class DiagnosticDomain:
    def _resolve_docref(self, env, fromdocname, builder, typ, target, node, contnode):
        try:
            resource_id = self.get_resource_id(env, builder, "table", "index", node)
        except ValueError:
            logger.warning(__("resource id is missing for %s: %s"), "table", "id1",
                           location=node)
            return contnode

    def get_resource_id(self, env, builder, figtype, docname, target_node):
        if builder.name == "pdf":
            return env.toc_resource_ids[docname][figtype][target_node["ids"][0]]
        return ()
"""
        html_test_content = """\
def test_resource_ids_without_numbered_toctree_warn(app, warning):
    app.build()
    warnings = warning.getvalue()
    assert "index.rst:55: WARNING: resource id is missing for section: index" in warnings
"""

        result = locate_code_structured(
            "DocSuite 3.3 upgrade started generating "
            "'WARNING: resource id is missing for table' warnings in "
            "singlepage or pdf builds; likely docref logic around tables and warnings",
            [
                CodeLocateFile(std_content, "viking://r/docsuite/domains/std.py"),
                CodeLocateFile(html_test_content, "viking://r/tests/test_build_html.py"),
            ],
            allow_viking_commands=True,
        )

        top = result.edit_candidates[0]
        assert any("logger.warning" in snippet["text"] for snippet in top.snippets)
        assert all("builder.name" not in snippet["text"] for snippet in top.snippets)
        assert all(
            symbol["name"] != "DiagnosticDomain.get_resource_id"
            for symbol in top.focus_symbols
        )

    def test_diagnostic_phrase_bonus_uses_generic_message_shape(self):
        assert (
            _diagnostic_phrase_bonus(
                'logger.warning(__("invalid value for %s: %s"), field, value)',
                {"invalid", "value", "field"},
            )
            > 0
        )
        assert (
            _diagnostic_phrase_bonus(
                "logger.warning(__('changed'), value)",
                {"changed"},
            )
            == 0
        )

    def test_plain_render_assertion_is_not_diagnostic_assertion(self):
        assert not _is_diagnostic_assertion_line(
            r"assert '\\docsuitecaption{The table title with a reference' in result"
        )

    def test_suggested_verification_prefers_edit_matching_top_behavior_test(self):
        lines = _format_verification_section(
            [
                _CodeLocateHit(file_name="viking://r/samplepkg/externals/_arff.py", score=200),
                _CodeLocateHit(file_name="viking://r/samplepkg/utils/pretty.py", score=180),
            ],
            [
                _CodeLocateHit(
                    file_name="viking://r/samplepkg/utils/tests/test_pretty.py",
                    score=300,
                )
            ],
        )

        assert "- static: python3 -m py_compile samplepkg/utils/pretty.py" in lines


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
