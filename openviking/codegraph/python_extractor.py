# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Python symbol and call extraction for the CodeGraph prototype."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from functools import lru_cache
from typing import Any, Optional

from tree_sitter_language_pack import get_parser

from openviking.codegraph.models import (
    ExtractedFile,
    GraphEdge,
    PendingCall,
    SourceFile,
    SymbolNode,
)

GRAPH_ID_SCHEMA_VERSION = 2


class SymbolCollisionError(ValueError):
    """Raised when two declarations map to the same stable symbol identity."""


def _hash_parts(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _node_text(source: bytes, node: Any) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _normalize_type(annotation: Optional[ast.expr]) -> str:
    if annotation is None:
        return "_"
    return re.sub(r"\s+", "", ast.unparse(annotation))


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, *, method: bool) -> str:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    removed_posonly_receiver = False
    if method and positional and positional[0].arg in {"self", "cls"}:
        removed_posonly_receiver = bool(args.posonlyargs and positional[0] is args.posonlyargs[0])
        positional = positional[1:]

    parts: list[str] = []
    posonly_count = max(0, len(args.posonlyargs) - int(removed_posonly_receiver))
    for index, arg in enumerate(positional):
        parts.append(f"p:{_normalize_type(arg.annotation)}")
        if posonly_count and index + 1 == posonly_count:
            parts.append("/")

    if args.vararg is not None:
        parts.append(f"*:{_normalize_type(args.vararg.annotation)}")
    elif args.kwonlyargs:
        parts.append("*")

    parts.extend(f"k:{_normalize_type(arg.annotation)}" for arg in args.kwonlyargs)
    if args.kwarg is not None:
        parts.append(f"**:{_normalize_type(args.kwarg.annotation)}")

    async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{async_prefix}({','.join(parts)})->{_normalize_type(node.returns)}"


def _signature_map(content: str) -> dict[tuple[int, str], str]:
    """Return signatures keyed by one-based declaration line and qualified name."""

    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Tree-sitter may support newer Python syntax than the running interpreter.
        return {}
    result: dict[tuple[int, str], str] = {}

    def walk(statements: list[ast.stmt], scope: tuple[str, ...], in_class: bool) -> None:
        for statement in statements:
            if isinstance(statement, ast.ClassDef):
                qualified_name = ".".join((*scope, statement.name))
                result[(statement.lineno, qualified_name)] = ""
                walk(statement.body, (*scope, statement.name), True)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = ".".join((*scope, statement.name))
                result[(statement.lineno, qualified_name)] = _python_signature(
                    statement,
                    method=in_class,
                )
                walk(statement.body, (*scope, statement.name), False)

    walk(tree.body, (), False)
    return result


@lru_cache(maxsize=1)
def _python_parser():
    return get_parser("python")


def _fallback_tree_sitter_signature(node: Any, source: bytes, *, method: bool) -> str:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return "()->_"

    parts: list[str] = []
    first_parameter = True
    for parameter in parameters.named_children:
        raw = _node_text(source, parameter).strip()
        parameter_name = raw.split(":", 1)[0].split("=", 1)[0].lstrip("*").strip()
        if method and first_parameter and parameter_name in {"self", "cls"}:
            first_parameter = False
            continue
        first_parameter = False
        prefix = "**" if raw.startswith("**") else ("*" if raw.startswith("*") else "p")
        type_node = parameter.child_by_field_name("type")
        annotation = (
            re.sub(r"\s+", "", _node_text(source, type_node)) if type_node is not None else "_"
        )
        parts.append(f"{prefix}:{annotation}")

    return_node = node.child_by_field_name("return_type")
    return_type = (
        re.sub(r"\s+", "", _node_text(source, return_node)) if return_node is not None else "_"
    )
    async_prefix = "async " if _node_text(source, node).lstrip().startswith("async ") else ""
    return f"{async_prefix}({','.join(parts)})->{return_type}"


def _stable_node_id(
    *,
    repo_id: str,
    file_path: str,
    qualified_name: str,
    kind: str,
    canonical_signature: str,
    declaration_ordinal: int,
) -> str:
    return _hash_parts(
        "codegraph-node",
        GRAPH_ID_SCHEMA_VERSION,
        repo_id,
        "python",
        file_path,
        qualified_name,
        kind,
        canonical_signature,
        declaration_ordinal,
    )


def extract_python_file(
    *,
    repo_id: str,
    graph_generation: str,
    file_id: int,
    source_file: SourceFile,
) -> ExtractedFile:
    """Extract Python declarations and call references from one immutable file."""

    relative_path = source_file.relative_path.replace("\\", "/").lstrip("/")
    if not relative_path or relative_path.startswith("../") or "/../" in relative_path:
        raise ValueError(f"invalid relative source path: {source_file.relative_path!r}")

    source = source_file.content.encode("utf-8")
    source_blob_oid = source_file.source_blob_oid or hashlib.sha256(source).hexdigest()
    signatures = _signature_map(source_file.content)
    syntax_tree = _python_parser().parse(source)
    if syntax_tree.root_node.has_error:
        raise ValueError(f"cannot index syntactically invalid Python file: {relative_path}")

    file_node_id = _stable_node_id(
        repo_id=repo_id,
        file_path=relative_path,
        qualified_name=relative_path,
        kind="file",
        canonical_signature="",
        declaration_ordinal=0,
    )
    file_node = SymbolNode(
        node_id=file_node_id,
        repo_id=repo_id,
        graph_generation=graph_generation,
        file_id=file_id,
        file_uri=source_file.uri,
        file_path=relative_path,
        language="python",
        kind="file",
        name=relative_path.rsplit("/", 1)[-1],
        qualified_name=relative_path,
        canonical_signature="",
        declaration_ordinal=0,
        start_line=1,
        end_line=max(1, len(source_file.content.splitlines())),
        source_blob_oid=source_blob_oid,
        content_hash=hashlib.sha256(source).hexdigest(),
        snippet="",
    )

    nodes: list[SymbolNode] = [file_node]
    edges: list[GraphEdge] = []
    calls: list[PendingCall] = []
    seen_ids = {file_node_id}
    declaration_counts: dict[tuple[str, str, str], int] = {}

    def add_declaration(
        node: Any,
        *,
        scope: tuple[str, ...],
        parent_id: str,
        parent_kind: str,
        containing_class: Optional[str],
    ) -> None:
        name_node = node.child_by_field_name("name")
        body_node = node.child_by_field_name("body")
        if name_node is None or body_node is None:
            return

        name = _node_text(source, name_node)
        qualified_name = ".".join((*scope, name))
        is_class = node.type == "class_definition"
        kind = "class" if is_class else ("method" if parent_kind == "class" else "function")
        canonical_signature = ""
        if not is_class:
            canonical_signature = signatures.get(
                (node.start_point.row + 1, qualified_name)
            ) or _fallback_tree_sitter_signature(
                node,
                source,
                method=kind == "method",
            )
        identity_key = (qualified_name, kind, canonical_signature)
        declaration_ordinal = declaration_counts.get(identity_key, 0)
        declaration_counts[identity_key] = declaration_ordinal + 1
        node_id = _stable_node_id(
            repo_id=repo_id,
            file_path=relative_path,
            qualified_name=qualified_name,
            kind=kind,
            canonical_signature=canonical_signature,
            declaration_ordinal=declaration_ordinal,
        )
        if node_id in seen_ids:
            raise SymbolCollisionError(
                f"symbol identity collision in {relative_path}: "
                f"{qualified_name}{canonical_signature}"
            )
        seen_ids.add(node_id)

        declaration = _node_text(source, node)
        snippet = "\n".join(declaration.splitlines()[:8])
        symbol = SymbolNode(
            node_id=node_id,
            repo_id=repo_id,
            graph_generation=graph_generation,
            file_id=file_id,
            file_uri=source_file.uri,
            file_path=relative_path,
            language="python",
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            canonical_signature=canonical_signature,
            declaration_ordinal=declaration_ordinal,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            source_blob_oid=source_blob_oid,
            content_hash=hashlib.sha256(declaration.encode("utf-8")).hexdigest(),
            snippet=snippet,
        )
        nodes.append(symbol)
        edges.append(
            GraphEdge(
                source_id=parent_id,
                target_id=node_id,
                target_name=qualified_name,
                kind="defines",
                resolution="resolved",
                confidence=1.0,
                evidence_file_id=file_id,
                evidence_line=node.start_point.row + 1,
            )
        )

        next_class = qualified_name if is_class else containing_class
        visit(
            body_node,
            scope=(*scope, name),
            parent_id=node_id,
            parent_kind=kind,
            containing_class=next_class,
            current_callable=None if is_class else symbol,
        )

    def add_call(node: Any, caller: SymbolNode) -> None:
        function = node.child_by_field_name("function")
        if function is None:
            return

        receiver: Optional[str] = None
        if function.type == "identifier":
            target_name = _node_text(source, function)
        elif function.type == "attribute":
            attribute = function.child_by_field_name("attribute")
            obj = function.child_by_field_name("object")
            if attribute is None:
                return
            target_name = _node_text(source, attribute)
            receiver = _node_text(source, obj) if obj is not None else None
        else:
            return

        calls.append(
            PendingCall(
                source_id=caller.node_id,
                source_qualified_name=caller.qualified_name,
                source_file_id=file_id,
                source_file_path=relative_path,
                containing_class=(
                    caller.qualified_name.rsplit(".", 1)[0]
                    if caller.kind == "method" and "." in caller.qualified_name
                    else None
                ),
                target_name=target_name,
                receiver=receiver,
                evidence_line=node.start_point.row + 1,
            )
        )

    def visit(
        node: Any,
        *,
        scope: tuple[str, ...],
        parent_id: str,
        parent_kind: str,
        containing_class: Optional[str],
        current_callable: Optional[SymbolNode],
    ) -> None:
        if node.type in {"class_definition", "function_definition"}:
            add_declaration(
                node,
                scope=scope,
                parent_id=parent_id,
                parent_kind=parent_kind,
                containing_class=containing_class,
            )
            return
        if node.type == "call" and current_callable is not None:
            add_call(node, current_callable)
        for child in node.named_children:
            visit(
                child,
                scope=scope,
                parent_id=parent_id,
                parent_kind=parent_kind,
                containing_class=containing_class,
                current_callable=current_callable,
            )

    visit(
        syntax_tree.root_node,
        scope=(),
        parent_id=file_node_id,
        parent_kind="file",
        containing_class=None,
        current_callable=None,
    )
    return ExtractedFile(
        file_node=file_node,
        nodes=tuple(nodes),
        edges=tuple(edges),
        pending_calls=tuple(calls),
    )
