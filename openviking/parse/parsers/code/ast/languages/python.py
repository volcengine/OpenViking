# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Python AST extractor using tree-sitter-python."""

from typing import List, Optional

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_string_child(body_node, content_bytes: bytes) -> str:
    """Extract docstring from the first expression_statement in a body."""
    if body_node is None:
        return ""
    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type in ("string", "concatenated_string"):
                    raw = _node_text(sub, content_bytes).strip()
                    # Strip quotes
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                            return raw[len(q):-len(q)].strip()
                    return raw
            break  # only check first expression_statement
    return ""


def _extract_function(node, content_bytes: bytes) -> FunctionSig:
    name = ""
    params = ""
    return_type = ""
    body_node = None

    for child in node.children:
        if child.type == "identifier":
            name = _node_text(child, content_bytes)
        elif child.type == "parameters":
            raw = _node_text(child, content_bytes).strip()
            # Remove surrounding parens
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            params = raw.strip()
        elif child.type == "type":
            return_type = _node_text(child, content_bytes).strip()
        elif child.type == "block":
            body_node = child

    docstring = _first_string_child(body_node, content_bytes)
    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_class(node, content_bytes: bytes) -> ClassSkeleton:
    name = ""
    bases: List[str] = []
    body_node = None

    for child in node.children:
        if child.type == "identifier":
            name = _node_text(child, content_bytes)
        elif child.type == "argument_list":
            # base classes
            for arg in child.children:
                if arg.type not in (",", "(", ")"):
                    bases.append(_node_text(arg, content_bytes).strip())
        elif child.type == "block":
            body_node = child

    docstring = _first_string_child(body_node, content_bytes)
    methods: List[FunctionSig] = []
    if body_node:
        for child in body_node.children:
            if child.type == "function_definition":
                methods.append(_extract_function(child, content_bytes))
            elif child.type == "decorated_definition":
                # decorated method
                for sub in child.children:
                    if sub.type == "function_definition":
                        methods.append(_extract_function(sub, content_bytes))

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


def _extract_imports(node, content_bytes: bytes) -> List[str]:
    """Flatten import node into module name strings."""
    results: List[str] = []
    if node.type == "import_statement":
        # import foo, bar
        for child in node.children:
            if child.type == "dotted_name":
                results.append(_node_text(child, content_bytes))
            elif child.type == "aliased_import":
                for sub in child.children:
                    if sub.type == "dotted_name":
                        results.append(_node_text(sub, content_bytes))
                        break
    elif node.type == "import_from_statement":
        # from foo import bar, baz
        module = ""
        names: List[str] = []
        for child in node.children:
            if child.type == "dotted_name" and not module:
                module = _node_text(child, content_bytes)
            elif child.type == "import_prefix":
                # relative imports like "from . import foo"
                module = _node_text(child, content_bytes)
            elif child.type == "wildcard_import":
                results.append(f"{module}.*")
                return results
            elif child.type == "dotted_name" and module:
                names.append(_node_text(child, content_bytes))
            elif child.type == "aliased_import":
                for sub in child.children:
                    if sub.type == "dotted_name":
                        names.append(_node_text(sub, content_bytes))
                        break

        if names:
            for n in names:
                results.append(f"{module}.{n}" if module else n)
        elif module:
            results.append(module)
    return results


class PythonExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        self._language = Language(tspython.language())
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        module_doc = ""
        imports: List[str] = []
        classes: List[ClassSkeleton] = []
        functions: List[FunctionSig] = []

        # Module docstring: first expression_statement at top level
        for child in root.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        raw = _node_text(sub, content_bytes).strip()
                        for q in ('"""', "'''", '"', "'"):
                            if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                                module_doc = raw[len(q):-len(q)].strip()
                                break
                        else:
                            module_doc = raw
                break  # only first statement
            elif child.type not in ("comment", "newline"):
                break

        for child in root.children:
            if child.type in ("import_statement", "import_from_statement"):
                imports.extend(_extract_imports(child, content_bytes))
            elif child.type == "class_definition":
                classes.append(_extract_class(child, content_bytes))
            elif child.type == "function_definition":
                functions.append(_extract_function(child, content_bytes))
            elif child.type == "decorated_definition":
                for sub in child.children:
                    if sub.type == "class_definition":
                        classes.append(_extract_class(sub, content_bytes))
                        break
                    elif sub.type == "function_definition":
                        functions.append(_extract_function(sub, content_bytes))
                        break

        return CodeSkeleton(
            file_name=file_name,
            language="Python",
            module_doc=module_doc,
            imports=imports,
            classes=classes,
            functions=functions,
        )
