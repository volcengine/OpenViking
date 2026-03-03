# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for AST-based code skeleton extraction."""

import pytest
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _python_extractor():
    from openviking.parse.parsers.code.ast.languages.python import PythonExtractor
    return PythonExtractor()


def _js_extractor():
    from openviking.parse.parsers.code.ast.languages.js_ts import JsTsExtractor
    return JsTsExtractor(lang="javascript")


def _go_extractor():
    from openviking.parse.parsers.code.ast.languages.go import GoExtractor
    return GoExtractor()


def _ts_extractor():
    from openviking.parse.parsers.code.ast.languages.js_ts import JsTsExtractor
    return JsTsExtractor(lang="typescript")


def _csharp_extractor():
    from openviking.parse.parsers.code.ast.languages.csharp import CSharpExtractor
    return CSharpExtractor()



# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

class TestPythonExtractor:
    SAMPLE = '''"""Module for parsing things.

This module provides utilities for parsing text content.
"""

import os
import sys
from typing import List


class MyParser:
    """A generic parser for text content.

    Handles both sync and async parsing flows.
    """

    def parse(self, source: str) -> List[str]:
        """Parse the source text.

        Args:
            source: The text to parse.

        Returns:
            List of parsed lines.
        """
        pass

    async def parse_async(
        self,
        source: str,
        encoding: str = "utf-8",
    ) -> List[str]:
        """Parse the source asynchronously.

        Args:
            source: The text to parse.
            encoding: The text encoding.

        Returns:
            List of parsed lines.
        """
        pass

    def _helper(self, text: str) -> str:
        """Internal helper method."""
        pass


def standalone(text: str) -> str:
    """A standalone utility function."""
    pass
'''

    def setup_method(self):
        self.e = _python_extractor()

    def test_module_doc(self):
        sk = self.e.extract("test.py\n\n", self.SAMPLE)
        assert "Module for parsing things" in sk.module_doc

    def test_imports(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        assert "os" in sk.imports
        assert "sys" in sk.imports
        assert any("List" in i for i in sk.imports)

    def test_class_extracted(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        assert len(sk.classes) == 1
        cls = sk.classes[0]
        assert cls.name == "MyParser"
        assert "generic parser" in cls.docstring

    def test_methods_extracted(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        methods = {m.name: m for m in sk.classes[0].methods}
        assert "parse" in methods
        assert methods["parse"].return_type == "List[str]"
        assert "parse_async" in methods
        assert "_helper" in methods

    def test_multiline_params(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        methods = {m.name: m for m in sk.classes[0].methods}
        # raw params may contain newlines, but to_text() must compact them
        assert "encoding" in methods["parse_async"].params
        text = sk.to_text()
        assert "\n  +" not in text.split("parse_async")[1].split("\n")[0]  # no newline inside the signature line

    def test_top_level_function(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        fns = {f.name for f in sk.functions}
        assert "standalone" in fns

    def test_to_text_compact(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# test.py [Python]" in text
        assert "class MyParser" in text
        assert "+ parse(" in text
        assert "def standalone" in text
        # only first line of docstring
        assert "Handles both sync" not in text
        assert "Args:" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("test.py", self.SAMPLE)
        text = sk.to_text(verbose=True)
        # full class docstring preserved
        assert "Handles both sync and async parsing flows." in text
        # full method docstring preserved
        assert "Args:" in text
        assert "Returns:" in text
        assert "List of parsed lines." in text
        # module doc still single-line with label
        assert 'module: "Module for parsing things.' in text


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

class TestJavaScriptExtractor:
    SAMPLE = '''
import React from "react";
import { useState, useEffect } from "react";

/**
 * Counter component.
 *
 * Maintains an internal count and exposes increment/decrement.
 */
class Counter extends React.Component {
  /**
   * Render the counter UI.
   *
   * @returns JSX element
   */
  render() {
    return null;
  }
}

/**
 * Add two numbers together.
 *
 * @param {number} a - First operand
 * @param {number} b - Second operand
 * @returns {number} Sum
 */
function add(a, b) {
  return a + b;
}
'''

    def setup_method(self):
        self.e = _js_extractor()

    def test_imports(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        assert "react" in sk.imports
        # both import statements point to "react" — should be deduplicated
        assert sk.imports.count("react") == 1

    def test_class_extracted(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        names = {c.name for c in sk.classes}
        assert "Counter" in names

    def test_class_docstring(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Counter")
        assert "Counter component" in cls.docstring

    def test_method_docstring(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Counter")
        methods = {m.name: m for m in cls.methods}
        assert "render" in methods
        assert "Render the counter UI" in methods["render"].docstring

    def test_function_extracted(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        names = {f.name for f in sk.functions}
        assert "add" in names

    def test_function_docstring(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        fns = {f.name: f for f in sk.functions}
        assert "Add two numbers together" in fns["add"].docstring

    def test_to_text_compact(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# app.js [JavaScript]" in text
        assert "class Counter" in text
        # only first docstring line in compact mode
        assert "Maintains an internal count" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("app.js", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "# app.js [JavaScript]" in text
        assert "class Counter" in text
        # full docstring in verbose mode
        assert "Maintains an internal count and exposes increment/decrement" in text

    def test_export_class(self):
        code = '''
/** Base utility class.
 *
 * Provides shared helper methods.
 */
export class Utils {
  /** Log a message to the console. */
  log(msg) { console.log(msg); }
}
'''
        sk = self.e.extract("utils.js", code)
        names = {c.name for c in sk.classes}
        assert "Utils" in names
        cls = next(c for c in sk.classes if c.name == "Utils")
        assert "Base utility class" in cls.docstring
        assert any(m.name == "log" for m in cls.methods)

    def test_arrow_function(self):
        code = '''
/** Double a number. */
const double = (n) => n * 2;

/** Negate a boolean. */
const negate = (b) => !b;
'''
        sk = self.e.extract("math.js", code)
        names = {f.name for f in sk.functions}
        assert "double" in names
        assert "negate" in names
        fns = {f.name: f for f in sk.functions}
        assert "Double a number" in fns["double"].docstring


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

class TestGoExtractor:
    SAMPLE = '''
package main

import (
    "fmt"
    "os"
)

// Server handles incoming HTTP connections.
type Server struct {
    host string
    port int
}

// NewServer creates a Server with the given host and port.
// Returns a pointer to the initialized Server.
func NewServer(host string, port int) *Server {
    return &Server{host: host, port: port}
}

//Start begins listening for connections.
func (s *Server) Start() error {
    fmt.Println("starting")
    return nil
}
'''

    def setup_method(self):
        self.e = _go_extractor()

    def test_imports(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        assert "fmt" in sk.imports
        assert "os" in sk.imports

    def test_struct_extracted(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        names = {c.name for c in sk.classes}
        assert "Server" in names

    def test_functions_extracted(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        names = {f.name for f in sk.functions}
        assert "NewServer" in names
        assert "Start" in names  # method_declaration is included alongside function_declaration

    def test_method_receiver_not_params(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        fns = {f.name: f for f in sk.functions}
        # (s *Server) is the receiver, not a parameter — must not appear in params
        assert "s *Server" not in fns["Start"].params
        assert fns["Start"].return_type == "error"

    def test_docstring_extracted(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        fns = {f.name: f for f in sk.functions}
        assert "NewServer creates a Server with the given host and port." in fns["NewServer"].docstring
        assert "Returns a pointer to the initialized Server." in fns["NewServer"].docstring
        structs = {c.name: c for c in sk.classes}
        assert "Server" in structs
        assert "Server handles incoming HTTP connections" in structs["Server"].docstring

    def test_to_text_compact(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# main.go [Go]" in text
        assert "NewServer" in text
        # only first line
        assert "Returns a pointer" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("main.go", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "# main.go [Go]" in text
        assert "NewServer" in text
        assert "Returns a pointer to the initialized Server." in text


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

def _java_extractor():
    from openviking.parse.parsers.code.ast.languages.java import JavaExtractor
    return JavaExtractor()


class TestJavaExtractor:
    SAMPLE = '''
import java.util.List;
import java.util.Optional;

/**
 * A simple calculator service.
 *
 * Supports basic arithmetic operations on integers.
 */
public class Calculator {

    /**
     * Add two integers.
     *
     * @param a first operand
     * @param b second operand
     * @return sum of a and b
     */
    public int add(int a, int b) {
        return a + b;
    }

    /**
     * Subtract b from a.
     *
     * @param a minuend
     * @param b subtrahend
     * @return difference
     */
    public int subtract(int a, int b) {
        return a - b;
    }
}
'''

    def setup_method(self):
        self.e = _java_extractor()

    def test_imports(self):
        sk = self.e.extract("Calculator.java\n", self.SAMPLE)
        assert any("List" in i for i in sk.imports)

    def test_class_extracted(self):
        sk = self.e.extract("Calculator.java", self.SAMPLE)
        assert len(sk.classes) == 1
        assert sk.classes[0].name == "Calculator"

    def test_class_docstring(self):
        sk = self.e.extract("Calculator.java", self.SAMPLE)
        doc = sk.classes[0].docstring
        assert "simple calculator service" in doc
        assert "Supports basic arithmetic" in doc

    def test_methods_extracted(self):
        sk = self.e.extract("Calculator.java", self.SAMPLE)
        methods = {m.name: m for m in sk.classes[0].methods}
        assert "add" in methods
        assert "subtract" in methods

    def test_method_docstring(self):
        sk = self.e.extract("Calculator.java", self.SAMPLE)
        methods = {m.name: m for m in sk.classes[0].methods}
        assert "Add two integers." in methods["add"].docstring
        assert "@param a first operand" in methods["add"].docstring
        assert "Subtract b from a." in methods["subtract"].docstring

    def test_to_text_compact(self):
        sk = self.e.extract("Calculator.java", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# Calculator.java [Java]" in text
        assert "class Calculator" in text
        assert "+ add(" in text
        assert "@param" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("Calculator.java", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "simple calculator service" in text
        assert "@param a first operand" in text
        assert "@return sum of a and b" in text


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------

class TestCSharpExtractor:
    SAMPLE = """
using System;
using System.Collections.Generic;

namespace MyApp.Services
{
    /// <summary>
    /// A simple calculator service.
    ///
    /// Supports basic arithmetic operations.
    /// </summary>
    public class Calculator
    {
        /// <summary>
        /// Add two integers.
        ///
        /// <param name=\"a\">First operand</param>
        /// <param name=\"b\">Second operand</param>
        /// <returns>Sum of a and b</returns>
        /// </summary>
        public int Add(int a, int b)
        {
            return a + b;
        }

        /// <summary>
        /// Subtract b from a.
        /// </summary>
        public int Subtract(int a, int b)
        {
            return a - b;
        }
    }
}
"""

    def setup_method(self):
        self.e = _csharp_extractor()

    def test_imports(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        assert "System" in sk.imports
        assert "System.Collections.Generic" in sk.imports

    def test_class_extracted(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        names = {c.name for c in sk.classes}
        assert "Calculator" in names

    def test_class_docstring(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Calculator")
        assert "simple calculator service" in cls.docstring
        assert "Supports basic arithmetic" in cls.docstring

    def test_methods_extracted(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Calculator")
        methods = {m.name: m for m in cls.methods}
        assert "Add" in methods
        assert "Subtract" in methods

    def test_method_docstring(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Calculator")
        methods = {m.name: m for m in cls.methods}
        assert "Add two integers." in methods["Add"].docstring
        assert "First operand" in methods["Add"].docstring

    def test_to_text_compact(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# Calculator.cs [C#]" in text
        assert "class Calculator" in text
        assert "+ Add(" in text
        assert "First operand" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("Calculator.cs", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "simple calculator service" in text
        assert "First operand" in text

    def test_file_scoped_namespace(self):
        code = '''
using System;

namespace MyApp.Services;

public class Calculator
{
    public int Add(int a, int b)
    {
        return a + b;
    }
}
'''
        sk = self.e.extract("Calculator.cs", code)
        names = {c.name for c in sk.classes}
        assert "Calculator" in names

    def test_property_accessor_signature(self):
        code = '''
public class Calculator
{
    /// <summary>
    /// Current result.
    /// </summary>
    public int Result { get; set; }
}
'''
        sk = self.e.extract("Calculator.cs", code)
        cls = next(c for c in sk.classes if c.name == "Calculator")
        methods = {m.name: m for m in cls.methods}
        assert "Result" in methods
        assert "get" in methods["Result"].params
        assert "set" in methods["Result"].params


# ---------------------------------------------------------------------------
# C/C++
# ---------------------------------------------------------------------------

def _cpp_extractor():
    from openviking.parse.parsers.code.ast.languages.cpp import CppExtractor
    return CppExtractor()


class TestCppExtractor:
    SAMPLE = '''
#include <string>
#include <vector>

/**
 * A simple stack data structure.
 *
 * Supports push, pop, and peek operations.
 */
class Stack {
public:
    /**
     * Push a value onto the stack.
     *
     * @param value The value to push
     */
    void push(int value);

    /**
     * Pop the top value from the stack.
     *
     * @return The popped value
     */
    int pop();
};

/**
 * Compute the sum of two integers.
 *
 * @param a First operand
 * @param b Second operand
 * @return Sum of a and b
 */
int add(int a, int b) {
    return a + b;
}
'''

    def setup_method(self):
        self.e = _cpp_extractor()

    def test_imports(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        assert "string" in sk.imports
        assert "vector" in sk.imports

    def test_class_extracted(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        names = {c.name for c in sk.classes}
        assert "Stack" in names

    def test_class_docstring(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Stack")
        assert "simple stack data structure" in cls.docstring
        assert "Supports push, pop" in cls.docstring

    def test_method_docstring(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Stack")
        methods = {m.name: m for m in cls.methods}
        assert "push" in methods
        assert "Push a value onto the stack." in methods["push"].docstring
        assert "@param value" in methods["push"].docstring

    def test_function_extracted(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        names = {f.name for f in sk.functions}
        assert "add" in names

    def test_function_docstring(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        fns = {f.name: f for f in sk.functions}
        assert "Compute the sum of two integers." in fns["add"].docstring
        assert "@param a First operand" in fns["add"].docstring

    def test_method_return_type(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "Stack")
        methods = {m.name: m for m in cls.methods}
        assert methods["push"].return_type == "void"
        assert methods["pop"].return_type == "int"

    def test_to_text_compact(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# stack.cpp [C/C++]" in text
        assert "class Stack" in text
        assert "@param" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("stack.cpp", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "simple stack data structure" in text
        assert "@param a First operand" in text
        assert "@return Sum of a and b" in text


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

def _rust_extractor():
    from openviking.parse.parsers.code.ast.languages.rust import RustExtractor
    return RustExtractor()


class TestRustExtractor:
    SAMPLE = '''
use std::collections::HashMap;
use std::io::{self, Read};

/// A key-value store backed by a HashMap.
///
/// Supports get, set, and delete operations.
pub struct Store {
    data: HashMap<String, String>,
}

impl Store {
    /// Create a new empty Store.
    ///
    /// # Examples
    /// ```
    /// let store = Store::new();
    /// ```
    pub fn new() -> Self {
        Store { data: HashMap::new() }
    }

    /// Get a value by key.
    ///
    /// Returns None if the key does not exist.
    pub fn get(&self, key: &str) -> Option<&String> {
        self.data.get(key)
    }
}

/// Compute the factorial of n.
///
/// # Panics
/// Panics if n is negative.
pub fn factorial(n: u64) -> u64 {
    if n == 0 { 1 } else { n * factorial(n - 1) }
}
'''

    def setup_method(self):
        self.e = _rust_extractor()

    def test_imports(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        assert any("HashMap" in i for i in sk.imports)

    def test_struct_extracted(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        names = {c.name for c in sk.classes}
        assert "Store" in names

    def test_struct_docstring(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        store = next(c for c in sk.classes if c.name == "Store")
        assert "key-value store" in store.docstring
        assert "Supports get, set, and delete" in store.docstring

    def test_impl_methods_docstring(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        impl = next(c for c in sk.classes if c.name == "impl Store")
        methods = {m.name: m for m in impl.methods}
        assert "new" in methods
        assert "Create a new empty Store." in methods["new"].docstring
        assert "Examples" in methods["new"].docstring
        assert "get" in methods
        assert "Returns None if the key does not exist." in methods["get"].docstring

    def test_function_extracted(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        names = {f.name for f in sk.functions}
        assert "factorial" in names

    def test_function_docstring(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        fns = {f.name: f for f in sk.functions}
        assert "Compute the factorial of n." in fns["factorial"].docstring
        assert "Panics" in fns["factorial"].docstring

    def test_to_text_compact(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# store.rs [Rust]" in text
        assert "Store" in text
        assert "Supports get, set" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("store.rs", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "key-value store" in text
        assert "Supports get, set, and delete operations." in text
        assert "Panics if n is negative." in text



# ---------------------------------------------------------------------------
# Skeleton.to_text() — verbose vs compact
# ---------------------------------------------------------------------------

class TestSkeletonToText:
    MULTILINE_DOC = "First line summary.\n\nMore details here.\nArgs:\n    x: an integer."

    def _make_skeleton(self):
        return CodeSkeleton(
            file_name="foo.py",
            language="Python",
            module_doc="A foo module.",
            imports=["os", "sys"],
            classes=[
                ClassSkeleton(
                    name="Foo", bases=["Base"],
                    docstring=self.MULTILINE_DOC,
                    methods=[FunctionSig("run", "self", "None", self.MULTILINE_DOC)]
                )
            ],
            functions=[FunctionSig("helper", "x: int", "bool", self.MULTILINE_DOC)],
        )

    def test_empty_skeleton(self):
        sk = CodeSkeleton(
            file_name="empty.py", language="Python",
            module_doc="", imports=[], classes=[], functions=[]
        )
        assert "# empty.py [Python]" in sk.to_text()

    def test_compact_only_first_line(self):
        text = self._make_skeleton().to_text(verbose=False)
        assert 'module: "A foo module."' in text
        assert "imports: os, sys" in text
        assert "class Foo(Base)" in text
        assert '"""First line summary."""' in text
        assert "+ run(self) -> None" in text
        assert "def helper(x: int) -> bool" in text
        # multi-line parts must NOT appear
        assert "More details here." not in text
        assert "Args:" not in text

    def test_verbose_full_docstring(self):
        text = self._make_skeleton().to_text(verbose=True)
        assert 'module: "A foo module."' in text
        assert "More details here." in text
        assert "Args:" in text
        assert "x: an integer." in text

    def test_verbose_single_line_doc_no_extra_quotes(self):
        sk = CodeSkeleton(
            file_name="bar.py", language="Python",
            module_doc="Single line.",
            imports=[],
            classes=[ClassSkeleton("Bar", [], "One liner.", [])],
            functions=[],
        )
        text = sk.to_text(verbose=True)
        # single-line docstring should still be inline: """One liner."""
        assert '"""One liner."""' in text
        # should NOT have a dangling """ on its own line
        lines = text.split("\n")
        assert not any(line.strip() == '"""' for line in lines)


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

class TestTypeScriptExtractor:
    SAMPLE = '''
import { Observable } from "rxjs";
import { HttpClient } from "@angular/common/http";

/**
 * Service for managing todos.
 *
 * Persists data to a remote API.
 */
class TodoService {
  /**
   * Get all todos.
   *
   * @returns array of todo strings
   */
  getAll(): string[] {
    return [];
  }

  /**
   * Add a new todo item.
   *
   * @param title the todo title
   */
  add(title: string): void {}
}

/**
 * Validate a todo title.
 *
 * Returns false if title is empty or too long.
 */
function validate(title: string): boolean {
  return title.length > 0 && title.length < 100;
}
'''

    def setup_method(self):
        self.e = _ts_extractor()

    def test_imports(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        assert "rxjs" in sk.imports
        assert "@angular/common/http" in sk.imports
        # no duplicates
        assert sk.imports.count("rxjs") == 1

    def test_class_extracted(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        names = {c.name for c in sk.classes}
        assert "TodoService" in names

    def test_class_docstring(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "TodoService")
        assert "Service for managing todos" in cls.docstring
        assert "Persists data to a remote API" in cls.docstring

    def test_methods_extracted(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "TodoService")
        methods = {m.name: m for m in cls.methods}
        assert "getAll" in methods
        assert "add" in methods

    def test_method_docstring(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        cls = next(c for c in sk.classes if c.name == "TodoService")
        methods = {m.name: m for m in cls.methods}
        assert "Get all todos" in methods["getAll"].docstring
        assert "Add a new todo item" in methods["add"].docstring

    def test_function_extracted(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        names = {f.name for f in sk.functions}
        assert "validate" in names

    def test_function_docstring(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        fns = {f.name: f for f in sk.functions}
        assert "Validate a todo title" in fns["validate"].docstring
        assert "Returns false if title is empty" in fns["validate"].docstring

    def test_to_text_compact(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        text = sk.to_text(verbose=False)
        assert "# todo.ts [TypeScript]" in text
        assert "TodoService" in text
        assert "Persists data to a remote API" not in text

    def test_to_text_verbose(self):
        sk = self.e.extract("todo.ts", self.SAMPLE)
        text = sk.to_text(verbose=True)
        assert "# todo.ts [TypeScript]" in text
        assert "Persists data to a remote API." in text
        assert "Returns false if title is empty or too long." in text


# ---------------------------------------------------------------------------
# ASTExtractor dispatch
# ---------------------------------------------------------------------------

class TestASTExtractorDispatch:
    def setup_method(self):
        from openviking.parse.parsers.code.ast.extractor import ASTExtractor
        self.extractor = ASTExtractor()

    def test_python_dispatch(self):
        code = 'def foo(x: int) -> str:\n    """Convert x to string."""\n    return str(x)\n'
        text = self.extractor.extract_skeleton("util.py", code)
        assert "# util.py [Python]" in text
        assert "def foo" in text

    def test_go_dispatch(self):
        code = 'package main\n\n// Run starts the app.\nfunc Run() error {\n    return nil\n}\n'
        text = self.extractor.extract_skeleton("main.go", code)
        assert "# main.go [Go]" in text
        assert "Run" in text

    def test_csharp_dispatch(self):
        code = 'namespace Demo;\n\npublic class Util { public int Add(int a, int b) { return a + b; } }\n'
        text = self.extractor.extract_skeleton("util.cs", code)
        assert "# util.cs [C#]" in text
        assert "class Util" in text

    def test_unknown_extension_returns_none(self):
        code = "def foo(x): pass\nclass Bar: pass\n"
        result = self.extractor.extract_skeleton("script.lua", code)
        assert result is None

    def test_never_raises(self):
        # empty content for supported language
        result = self.extractor.extract_skeleton("empty.py", "")
        assert result is None or isinstance(result, str)
        # unsupported extension → None, no exception
        result = self.extractor.extract_skeleton("file.xyz123", "\x00\x01\x02binary")
        assert result is None

    def test_verbose_propagated(self):
        code = 'def foo():\n    """Summary line.\n\n    Detail here.\n    """\n    pass\n'
        compact = self.extractor.extract_skeleton("m.py", code, verbose=False)
        verbose = self.extractor.extract_skeleton("m.py", code, verbose=True)
        assert "Detail here." not in compact
        assert "Detail here." in verbose
