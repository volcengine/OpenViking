# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""C# AST extractor using tree-sitter-c-sharp."""

import re
from typing import List

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _parse_doc_comment(raw: str) -> str:
    """Strip XML doc comment markers (/// or /** */) and extract text from XML tags."""
    raw = raw.strip()
    if raw.startswith("///"):
        lines = raw.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("///"):
                stripped = stripped[3:].strip()
            if stripped:
                cleaned.append(stripped)
        raw = " ".join(cleaned)
    elif raw.startswith("/**"):
        raw = raw[3:]
        if raw.endswith("*/"):
            raw = raw[:-2]
        lines = [l.strip().lstrip("*").strip() for l in raw.split("\n")]
        raw = "\n".join(l for l in lines if l).strip()
    # Remove XML tags
    raw = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?/?>", "", raw)
    # Normalize whitespace
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _preceding_doc(siblings: list, idx: int, content_bytes: bytes) -> str:
    """Return XML doc comment immediately before siblings[idx], or ''."""
    if idx == 0:
        return ""
    comments = []
    for i in range(idx - 1, -1, -1):
        prev = siblings[i]
        if prev.type == "comment":
            text = _node_text(prev, content_bytes)
            if text.strip().startswith("///") or text.strip().startswith("/**"):
                comments.insert(0, _parse_doc_comment(text))
            else:
                break
        elif prev.type in ("preprocessor_directive", "nullable_directive"):
            continue
        else:
            break
    return "\n".join(comments) if comments else ""


def _extract_method(node, content_bytes: bytes, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""

    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "void_keyword":
            return_type = "void"
        elif child.type in ("predefined_type", "type_identifier", "generic_name"):
            if not return_type:
                return_type = _node_text(child, content_bytes)
        elif child.type == "parameter_list":
            raw = _node_text(child, content_bytes).strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            params = raw.strip()

    if node.type == "property_declaration":
        for child in node.children:
            if child.type == "accessor_list":
                accessors = []
                for acc in child.children:
                    if acc.type == "accessor_declaration":
                        accessor_name = ""
                        name_node = acc.child_by_field_name("name")
                        if name_node is not None:
                            accessor_name = _node_text(name_node, content_bytes).strip()
                        else:
                            for sub in acc.children:
                                if sub.type in ("get", "set", "init"):
                                    accessor_name = sub.type
                                    break
                        if accessor_name in ("get", "set", "init"):
                            accessors.append(accessor_name)
                if accessors:
                    params = f"{{ {' '.join(accessors)} }}"

    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_class(node, content_bytes: bytes, docstring: str = "") -> ClassSkeleton:
    name = ""
    bases: List[str] = []
    body_node = None

    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "base_list":
            for sub in child.children:
                if sub.type in ("type_identifier", "identifier"):
                    bases.append(_node_text(sub, content_bytes))
        elif child.type == "declaration_list":
            body_node = child

    methods: List[FunctionSig] = []
    if body_node:
        siblings = list(body_node.children)
        for idx, child in enumerate(siblings):
            if child.type in ("method_declaration", "constructor_declaration"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                methods.append(_extract_method(child, content_bytes, docstring=doc))
            elif child.type == "property_declaration":
                doc = _preceding_doc(siblings, idx, content_bytes)
                methods.append(_extract_method(child, content_bytes, docstring=doc))

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


class CSharpExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_c_sharp as tscsharp
        from tree_sitter import Language, Parser

        self._language = Language(tscsharp.language())
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        imports: List[str] = []
        classes: List[ClassSkeleton] = []
        functions: List[FunctionSig] = []

        siblings = list(root.children)
        for idx, child in enumerate(siblings):
            if child.type == "using_directive":
                for sub in child.children:
                    if sub.type == "identifier":
                        imports.append(_node_text(sub, content_bytes))
                    elif sub.type == "qualified_name":
                        imports.append(_node_text(sub, content_bytes))
            elif child.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
                for sub in child.children:
                    if sub.type == "declaration_list":
                        ns_siblings = list(sub.children)
                        for ns_idx, ns_child in enumerate(ns_siblings):
                            if ns_child.type in (
                                "class_declaration",
                                "interface_declaration",
                                "struct_declaration",
                                "record_declaration",
                            ):
                                doc = _preceding_doc(ns_siblings, ns_idx, content_bytes)
                                classes.append(
                                    _extract_class(ns_child, content_bytes, docstring=doc)
                                )
            elif child.type in (
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
            ):
                doc = _preceding_doc(siblings, idx, content_bytes)
                classes.append(_extract_class(child, content_bytes, docstring=doc))

        return CodeSkeleton(
            file_name=file_name,
            language="C#",
            module_doc="",
            imports=imports,
            classes=classes,
            functions=functions,
        )
