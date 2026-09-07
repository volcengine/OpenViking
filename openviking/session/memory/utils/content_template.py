# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Restricted Markdown templates published through the account management API.

Deployment-owned templates keep their existing renderer. Only account overrides
use this contract, both at publication and when rendering the extraction snapshot.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from jinja2 import StrictUndefined, TemplateError, meta, nodes
from jinja2.runtime import LoopContext
from jinja2.sandbox import ImmutableSandboxedEnvironment

MAX_CONTENT_TEMPLATE_BYTES = 64 * 1024
MAX_CONTENT_OUTPUT_BYTES = 1024 * 1024
CONTENT_TEMPLATE_FIELDS = {
    "events": ("event_name", "goal", "summary", "ranges"),
    "soul": ("core_truths", "boundaries", "vibe", "continuity"),
    "identity": ("name", "creature", "vibe", "emoji", "avatar", "introduction"),
}
# Positional argument counts; these helpers only read the current extraction context.
_EVENT_METHODS = {
    "get_resource_event_content": (2,),
    "get_first_message_time_from_ranges": (1,),
    "get_first_message_time_with_weekday_from_ranges": (1,),
    "get_event_content": (2, 3),
    "get_year": (1,),
    "get_month": (1,),
    "get_day": (1,),
}
_FILTERS = {"default", "trim", "lower", "upper", "length"}
_TESTS = {"defined", "undefined", "none", "string"}
_LOOP_ATTRIBUTES = {"index", "index0", "first", "last", "length"}
_RESERVED_METADATA = re.compile(r"<!--\s*MEMORY_FIELDS\b")
_NODES = (
    nodes.Template,
    nodes.Output,
    nodes.TemplateData,
    nodes.Name,
    nodes.Const,
    nodes.If,
    nodes.For,
    nodes.Assign,
    nodes.List,
    nodes.Tuple,
    nodes.Getattr,
    nodes.Call,
    nodes.Filter,
    nodes.Test,
    nodes.Compare,
    nodes.Operand,
    nodes.And,
    nodes.Or,
    nodes.Not,
    nodes.CondExpr,
)


class ContentTemplateError(ValueError):
    def __init__(self, reason: str, line: int = 0):
        self.reason = reason
        self.line = line
        super().__init__(f"content_template: {reason}" + (f" (line {line})" if line else ""))


class _ContentEnvironment(ImmutableSandboxedEnvironment):
    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        return isinstance(obj, LoopContext) and attr in _LOOP_ATTRIBUTES


def _environment() -> _ContentEnvironment:
    env = _ContentEnvironment(autoescape=False, undefined=StrictUndefined)
    env.globals.clear()
    env.filters = {name: env.filters[name] for name in _FILTERS}
    env.tests = {name: env.tests[name] for name in _TESTS}
    return env


def _parse(template: str, memory_type: str, env: _ContentEnvironment) -> nodes.Template:
    if memory_type not in CONTENT_TEMPLATE_FIELDS:
        raise ContentTemplateError("unsupported_memory_type")
    if not isinstance(template, str) or not template.strip():
        raise ContentTemplateError("empty_template")
    if len(template.encode("utf-8")) > MAX_CONTENT_TEMPLATE_BYTES:
        raise ContentTemplateError("template_too_large")
    if _RESERVED_METADATA.search(template):
        raise ContentTemplateError("reserved_metadata")
    try:
        tree = env.parse(template)
        allowed = set(CONTENT_TEMPLATE_FIELDS[memory_type])
        if memory_type == "events":
            allowed.add("extract_context")
        all_nodes = list(tree.find_all(nodes.Node))
        if len(all_nodes) > 2048:
            raise ContentTemplateError("template_too_complex")
        callable_attributes = {id(n.node) for n in all_nodes if isinstance(n, nodes.Call)}
        attribute_owners = {id(n.node) for n in all_nodes if isinstance(n, nodes.Getattr)}
        loop_lists = [n.iter for n in all_nodes if isinstance(n, nodes.For)]
        literal_containers = {id(n) for n in loop_lists}
        for container in loop_lists:
            if isinstance(container, (nodes.List, nodes.Tuple)):
                literal_containers.update(
                    id(n) for n in container.items if isinstance(n, nodes.Tuple)
                )
        for node in all_nodes:
            if not isinstance(node, _NODES):
                raise ContentTemplateError("unsupported_syntax", node.lineno)
            if isinstance(node, nodes.Name) and node.ctx == "store":
                if node.name in allowed | {"extract_context", "loop"} or node.name.startswith("_"):
                    raise ContentTemplateError("reserved_variable", node.lineno)
            if (
                isinstance(node, nodes.Name)
                and node.name == "extract_context"
                and id(node) not in attribute_owners
            ):
                raise ContentTemplateError("unsupported_attribute", node.lineno)
            if (
                isinstance(node, (nodes.List, nodes.Tuple))
                and getattr(node, "ctx", "load") != "store"
                and id(node) not in literal_containers
            ):
                raise ContentTemplateError("unsupported_syntax", node.lineno)
            if isinstance(node, nodes.Getattr):
                owner = node.node.name if isinstance(node.node, nodes.Name) else None
                helper = (
                    owner == "extract_context"
                    and node.attr in _EVENT_METHODS
                    and memory_type == "events"
                    and id(node) in callable_attributes
                )
                if not helper and not (owner == "loop" and node.attr in _LOOP_ATTRIBUTES):
                    raise ContentTemplateError("unsupported_attribute", node.lineno)
            if isinstance(node, nodes.Call):
                _validate_call(node, memory_type)
            if isinstance(node, (nodes.Filter, nodes.Test)):
                names = _FILTERS if isinstance(node, nodes.Filter) else _TESTS
                if node.name not in names or node.kwargs or node.dyn_args or node.dyn_kwargs:
                    raise ContentTemplateError("unsupported_filter_or_test", node.lineno)
                max_args = 2 if node.name == "default" else 0
                if len(node.args) > max_args:
                    raise ContentTemplateError("invalid_arguments", node.lineno)
            if isinstance(node, nodes.For):
                # Iterating over an explicit field list is sufficient for Markdown
                # sections. No range(), message-sized loops, nesting or recursion.
                if (
                    node.recursive
                    or not isinstance(node.iter, (nodes.List, nodes.Tuple))
                    or len(node.iter.items) > 32
                    or list(node.find_all(nodes.For))
                ):
                    raise ContentTemplateError("unsupported_loop", node.lineno)
        # meta uses Jinja's compiler, which can constant-fold expressions. Only
        # invoke it after rejecting arithmetic, calls and other unsupported AST.
        unknown = meta.find_undeclared_variables(tree) - allowed
        if unknown:
            line = next(n.lineno for n in tree.find_all(nodes.Name) if n.name in unknown)
            raise ContentTemplateError("unknown_variable", line)
        return tree
    except TemplateError as exc:
        raise ContentTemplateError("invalid_jinja", getattr(exc, "lineno", 0) or 0) from exc
    except RecursionError as exc:
        raise ContentTemplateError("template_too_complex") from exc


def _validate_call(node: nodes.Call, memory_type: str) -> None:
    target = node.node
    if (
        memory_type != "events"
        or not isinstance(target, nodes.Getattr)
        or not isinstance(target.node, nodes.Name)
        or target.node.name != "extract_context"
        or target.attr not in _EVENT_METHODS
    ):
        raise ContentTemplateError("unsupported_call", node.lineno)
    if (
        node.kwargs
        or node.dyn_args
        or node.dyn_kwargs
        or len(node.args) not in _EVENT_METHODS[target.attr]
    ):
        raise ContentTemplateError("invalid_arguments", node.lineno)
    # Never allow a template to fabricate an unbounded message-index range.
    ranges: nodes.Expr | None = node.args[0]
    if isinstance(ranges, nodes.Filter) and ranges.name == "default":
        if ranges.args and not (
            isinstance(ranges.args[0], nodes.Const) and ranges.args[0].value == ""
        ):
            raise ContentTemplateError("invalid_ranges", node.lineno)
        ranges = ranges.node
    if not isinstance(ranges, nodes.Name) or ranges.name != "ranges":
        raise ContentTemplateError("invalid_ranges", node.lineno)
    if len(node.args) == 3:
        ratio = node.args[2]
        if (
            not isinstance(ratio, nodes.Const)
            or type(ratio.value) not in (int, float)
            or not 0 <= ratio.value <= 1
        ):
            raise ContentTemplateError("invalid_ratio", node.lineno)


def validate_content_template(template: str, memory_type: str) -> None:
    _parse(template, memory_type, _environment())


def render_content_template(
    template: str, memory_type: str, fields: Mapping[str, Any], extract_context: Any = None
) -> str:
    env = _environment()
    tree = _parse(template, memory_type, env)
    # Do not expose metadata, request context, URI helpers, or arbitrary objects.
    values: dict[str, Any] = {
        name: fields.get(name) or "" for name in CONTENT_TEMPLATE_FIELDS[memory_type]
    }
    if any(
        not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CONTENT_OUTPUT_BYTES
        for value in values.values()
    ):
        raise ContentTemplateError("invalid_field_value")
    if memory_type == "events":
        values["extract_context"] = {
            name: getattr(extract_context, name)
            for name in _EVENT_METHODS
            if extract_context is not None and hasattr(extract_context, name)
        }
    try:
        compiled = env.from_string(tree)
        chunks = []
        size = 0
        for chunk in compiled.generate(**values):
            size += len(chunk.encode("utf-8"))
            if size > MAX_CONTENT_OUTPUT_BYTES:
                raise ContentTemplateError("output_too_large")
            chunks.append(chunk)
        rendered = "".join(chunks).strip()
        if _RESERVED_METADATA.search(rendered):
            raise ContentTemplateError("reserved_metadata")
        return rendered
    except TemplateError as exc:
        # A runtime error must fail this write, not silently persist a blank body.
        raise ContentTemplateError("render_failed") from exc
