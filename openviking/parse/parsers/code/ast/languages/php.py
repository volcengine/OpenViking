# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PHP AST extractor using tree-sitter-php."""

from __future__ import annotations

from typing import List, Optional

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _parse_comment(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("/**") or raw.startswith("/*"):
        raw = raw[3:] if raw.startswith("/**") else raw[2:]
        if raw.endswith("*/"):
            raw = raw[:-2]
        lines = [l.strip().lstrip("*").strip() for l in raw.split("\n")]
        return "\n".join(l for l in lines if l).strip()
    if raw.startswith("//"):
        return raw[2:].strip()
    if raw.startswith("#"):
        return raw[1:].strip()
    return raw


def _preceding_doc(siblings: list, idx: int, content_bytes: bytes) -> str:
    if idx <= 0:
        return ""

    adjacent: List[str] = []
    current_start_row = siblings[idx].start_point[0]
    i = idx - 1
    while i >= 0 and siblings[i].type == "comment":
        if current_start_row - siblings[i].end_point[0] > 1:
            break
        adjacent.insert(0, _node_text(siblings[i], content_bytes).strip())
        current_start_row = siblings[i].start_point[0]
        i -= 1

    if not adjacent:
        return ""

    phpdocs = [c for c in adjacent if c.lstrip().startswith("/**")]
    chosen = phpdocs if phpdocs else adjacent
    parsed = [_parse_comment(c) for c in chosen]
    return "\n".join(p for p in parsed if p).strip()


def _strip_parens(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return raw.strip()


def _first_named_child_text(node, types: set, content_bytes: bytes) -> str:
    for child in node.children:
        if child.type in types:
            return _node_text(child, content_bytes).strip()
    return ""


def _collect_typeish_tokens(node, content_bytes: bytes) -> List[str]:
    out: List[str] = []

    def _walk(n) -> None:
        if n.type in (
            "qualified_name",
            "namespace_name",
            "name",
            "identifier",
        ):
            txt = _node_text(n, content_bytes).strip()
            if txt and txt not in out:
                out.append(txt)
        for c in n.children:
            _walk(c)

    _walk(node)
    return out


def _normalize_name(raw: str) -> str:
    raw = raw.strip()
    while raw.startswith("\\"):
        raw = raw[1:]
    return raw


def _extract_use_declaration(node, content_bytes: bytes) -> List[str]:
    base_prefix = ""
    group_node = None
    for child in node.children:
        if child.type == "namespace_use_group":
            group_node = child
            break
    if group_node is not None:
        base_node = None
        for child in node.children:
            if child.type == "namespace_name":
                base_node = child
                break
        if base_node is not None:
            base = _normalize_name(_node_text(base_node, content_bytes)).rstrip("\\")
            base_prefix = f"{base}\\" if base else ""

    def _clause_name(clause) -> str:
        name_node = None
        for sub in clause.children:
            if sub.type == "qualified_name":
                name_node = sub
                break
        if name_node is None:
            for sub in clause.children:
                if sub.type == "name":
                    name_node = sub
                    break
        return _normalize_name(_node_text(name_node, content_bytes)) if name_node is not None else ""

    clauses = []
    if group_node is not None:
        for sub in group_node.children:
            if sub.type == "namespace_use_clause":
                clauses.append(sub)
    else:
        for sub in node.children:
            if sub.type == "namespace_use_clause":
                clauses.append(sub)

    out: List[str] = []
    for clause in clauses:
        name = _clause_name(clause)
        if not name:
            continue
        out.append(f"{base_prefix}{name}" if base_prefix else name)

    return out


def _extract_function_like(node, content_bytes: bytes, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""

    name_node = node.child_by_field_name("name")
    if name_node is not None:
        name = _node_text(name_node, content_bytes).strip()
    if not name:
        name = _first_named_child_text(node, {"name", "identifier"}, content_bytes)

    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        params_node = node.child_by_field_name("parameters_list")
    if params_node is None:
        for child in node.children:
            if child.type in ("formal_parameters", "parameters", "parameter_list"):
                params_node = child
                break
    if params_node is not None:
        params = _strip_parens(_node_text(params_node, content_bytes))

    ret_node = node.child_by_field_name("return_type")
    if ret_node is None:
        ret_node = node.child_by_field_name("type")
    if ret_node is not None:
        return_type = _node_text(ret_node, content_bytes).strip().lstrip(":").strip()
    else:
        for child in node.children:
            if child.type in (
                "nullable_type",
                "union_type",
                "intersection_type",
                "primitive_type",
                "qualified_name",
                "namespace_name",
                "named_type",
            ):
                return_type = _node_text(child, content_bytes).strip().lstrip(":").strip()
                if return_type:
                    break

    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_class(
    node, content_bytes: bytes, docstring: str = "", name_prefix: str = ""
) -> ClassSkeleton:
    name = ""
    bases: List[str] = []
    body_node = None

    name_node = node.child_by_field_name("name")
    if name_node is not None:
        name = _node_text(name_node, content_bytes).strip()
    if not name:
        name = _first_named_child_text(node, {"name", "identifier"}, content_bytes)
    if name_prefix:
        name = f"{name_prefix}{name}" if name else name_prefix.strip()

    for child in node.children:
        if child.type in ("base_clause", "extends_clause", "implements_clause", "class_interface_clause"):
            for b in _collect_typeish_tokens(child, content_bytes):
                if b not in bases:
                    bases.append(b)
        elif child.type in ("declaration_list", "class_body", "interface_body", "trait_body"):
            body_node = child
        elif body_node is None and child.type.endswith("_body"):
            body_node = child

    methods: List[FunctionSig] = []
    if body_node is not None:
        siblings = list(body_node.children)
        for idx, child in enumerate(siblings):
            if child.type == "use_declaration":
                for sub in child.children:
                    if sub.type in ("name", "qualified_name"):
                        t = _normalize_name(_node_text(sub, content_bytes))
                        if t and t not in bases:
                            bases.append(t)
            elif child.type in ("method_declaration", "method_definition"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                methods.append(_extract_function_like(child, content_bytes, docstring=doc))

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


def _dedup_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for i in items:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _leading_module_doc(siblings: list, content_bytes: bytes) -> str:
    for child in siblings:
        if child.type in ("php_tag", "text"):
            continue
        if child.type == "comment":
            raw = _node_text(child, content_bytes).strip()
            if raw.startswith("/**"):
                return _parse_comment(raw)
            continue
        break
    return ""


def _process_siblings(
    siblings: list,
    content_bytes: bytes,
    imports: List[str],
    classes: List[ClassSkeleton],
    functions: List[FunctionSig],
) -> None:
    for idx, child in enumerate(siblings):
        if child.type == "namespace_use_declaration":
            imports.extend(_extract_use_declaration(child, content_bytes))
        elif child.type == "class_declaration":
            doc = _preceding_doc(siblings, idx, content_bytes)
            classes.append(_extract_class(child, content_bytes, docstring=doc, name_prefix=""))
        elif child.type == "interface_declaration":
            doc = _preceding_doc(siblings, idx, content_bytes)
            classes.append(_extract_class(child, content_bytes, docstring=doc, name_prefix="interface "))
        elif child.type == "trait_declaration":
            doc = _preceding_doc(siblings, idx, content_bytes)
            classes.append(_extract_class(child, content_bytes, docstring=doc, name_prefix="trait "))
        elif child.type == "enum_declaration":
            doc = _preceding_doc(siblings, idx, content_bytes)
            classes.append(_extract_class(child, content_bytes, docstring=doc, name_prefix="enum "))
        elif child.type in ("function_definition", "function_declaration"):
            doc = _preceding_doc(siblings, idx, content_bytes)
            functions.append(_extract_function_like(child, content_bytes, docstring=doc))
        elif child.type == "expression_statement":
            assign = None
            for sub in child.children:
                if sub.type == "assignment_expression":
                    assign = sub
                    break
            if assign is not None:
                left = None
                right = None
                for sub in assign.children:
                    if left is None and sub.type == "variable_name":
                        left = sub
                    elif right is None and sub.type in ("anonymous_function", "arrow_function"):
                        right = sub
                if left is not None and right is not None:
                    doc = _preceding_doc(siblings, idx, content_bytes)
                    sig = _extract_function_like(right, content_bytes, docstring=doc)
                    sig.name = _node_text(left, content_bytes).strip()
                    functions.append(sig)
        elif child.type == "namespace_definition":
            for sub in child.children:
                if sub.type in ("declaration_list", "compound_statement"):
                    _process_siblings(list(sub.children), content_bytes, imports, classes, functions)


class PhpExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_php as tsphp
        from tree_sitter import Language, Parser

        self._language = Language(tsphp.language_php())
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        imports: List[str] = []
        classes: List[ClassSkeleton] = []
        functions: List[FunctionSig] = []

        siblings = list(root.children)
        module_doc = _leading_module_doc(siblings, content_bytes)
        _process_siblings(siblings, content_bytes, imports, classes, functions)

        return CodeSkeleton(
            file_name=file_name,
            language="PHP",
            module_doc=module_doc,
            imports=_dedup_keep_order(imports),
            classes=classes,
            functions=functions,
        )
