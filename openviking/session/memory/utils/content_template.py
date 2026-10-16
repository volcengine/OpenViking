# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Restricted Markdown templates published through the account management API.

Deployment-owned templates and exact inherited copies keep their existing renderer.
Only account bodies differing from the current deployment defaults use this contract,
both at publication and when rendering the extraction snapshot.
"""

from __future__ import annotations

import re
from functools import partial
from typing import Any, Callable, Mapping

from jinja2 import StrictUndefined, TemplateError, Undefined, meta, nodes
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
_STRING_METHODS = {"upper", "lower", "strip"}
_STRING_FILTERS = {"upper": "upper", "lower": "lower", "trim": "strip"}
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
    def getattr(self, obj: Any, attribute: str) -> Any:
        # Check before Jinja looks up the attribute (or falls back to a mapping
        # key). A same-named method/property on another object is not trusted.
        if attribute in _STRING_METHODS and type(obj) is not str:
            return self.unsafe_undefined(obj, attribute)
        return super().getattr(obj, attribute)

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        return (type(obj) is str and attr in _STRING_METHODS) or (
            isinstance(obj, LoopContext) and attr in _LOOP_ATTRIBUTES
        )


def _string_filter(method: str, value: Any) -> str:
    # Unlike Jinja's built-ins, do not coerce arbitrary objects through __str__
    # or invoke overridden methods on string subclasses.
    if type(value) is not str:
        raise TemplateError("String filters require a plain string")
    return getattr(value, method)()


def _default_filter(value: Any, default_value: str = "") -> str | None:
    # Match Jinja's undefined-only fallback, including None/empty-string behavior.
    # Do not test truthiness or coerce objects supplied by context helpers.
    if isinstance(value, Undefined):
        return default_value
    if value is None or type(value) is str:
        return value
    raise TemplateError("Default filter requires a plain string, None or undefined")


def _environment() -> _ContentEnvironment:
    env = _ContentEnvironment(autoescape=False, undefined=StrictUndefined)
    env.globals.clear()
    env.filters = {
        name: partial(_string_filter, method) for name, method in _STRING_FILTERS.items()
    }
    env.filters["default"] = _default_filter
    env.tests = {name: env.tests[name] for name in _TESTS}
    return env


def _parse(
    template: str,
    memory_type: str,
    env: _ContentEnvironment,
    *,
    variables: frozenset[str] | None = None,
    max_template_bytes: int = MAX_CONTENT_TEMPLATE_BYTES,
) -> nodes.Template:
    # Descriptions reuse the same syntax/sandbox with their own variable and
    # source-size contract. They never receive Events helpers or body fields.
    if variables is None and memory_type not in CONTENT_TEMPLATE_FIELDS:
        raise ContentTemplateError("unsupported_memory_type")
    if not isinstance(template, str) or not template.strip():
        raise ContentTemplateError("empty_template")
    if len(template.encode("utf-8")) > max_template_bytes:
        raise ContentTemplateError("template_too_large")
    if _RESERVED_METADATA.search(template):
        raise ContentTemplateError("reserved_metadata")
    try:
        tree = env.parse(template)
        allowed = set(variables if variables is not None else CONTENT_TEMPLATE_FIELDS[memory_type])
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
            if isinstance(node, nodes.Filter):
                if node.name not in {*_STRING_FILTERS, "default"}:
                    raise ContentTemplateError("unsupported_filter", node.lineno)
                if node.kwargs or node.dyn_args or node.dyn_kwargs:
                    raise ContentTemplateError("invalid_arguments", node.lineno)
                if node.name == "default":
                    if len(node.args) > 1 or any(
                        not isinstance(arg, nodes.Const) or type(arg.value) is not str
                        for arg in node.args
                    ):
                        raise ContentTemplateError("invalid_arguments", node.lineno)
                elif node.args:
                    raise ContentTemplateError("invalid_arguments", node.lineno)
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
                string_method = (
                    node.attr in _STRING_METHODS
                    and owner not in {"extract_context", "loop"}
                    and id(node) in callable_attributes
                )
                if not (helper or string_method) and not (
                    owner == "loop" and node.attr in _LOOP_ATTRIBUTES
                ):
                    raise ContentTemplateError("unsupported_attribute", node.lineno)
            if isinstance(node, nodes.Call):
                _validate_call(node, memory_type)
            if isinstance(node, nodes.Test):
                if node.name not in _TESTS or node.kwargs or node.dyn_args or node.dyn_kwargs:
                    raise ContentTemplateError("unsupported_test", node.lineno)
                if node.args:
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
    if isinstance(target, nodes.Getattr) and target.attr in _STRING_METHODS:
        if node.args or node.kwargs or node.dyn_args or node.dyn_kwargs:
            raise ContentTemplateError("invalid_arguments", node.lineno)
        # The receiver may be a field, local, literal, or another allowed call.
        # Its exact runtime type is checked before attribute lookup by the sandbox.
        return
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
    # Range expressions follow the same syntax rules as other expressions.
    # Their evaluated values are checked before calling an extraction helper.
    if len(node.args) == 3:
        ratio = node.args[2]
        if (
            not isinstance(ratio, nodes.Const)
            or type(ratio.value) not in (int, float)
            or not 0 <= ratio.value <= 1
        ):
            raise ContentTemplateError("invalid_ratio", node.lineno)


def _call_event_helper(
    env: _ContentEnvironment,
    helper: Callable[..., Any],
    original_ranges: str,
    ranges: Any,
    *args: Any,
) -> Any:
    # Validate values, not a particular AST shape: aliases, conditions and
    # filters are fine, but cannot change which source messages a helper reads.
    # Check exact types before equality to avoid invoking user-defined methods.
    if (
        type(ranges) is not str
        or type(original_ranges) is not str
        or ranges not in ("", original_ranges)
    ):
        raise ContentTemplateError("invalid_ranges")
    # Wrapping a helper must not bypass its original sandbox callable flags.
    if not env.is_safe_callable(helper):
        raise TemplateError("Unsafe event helper")
    return helper(ranges, *args)


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
            name: partial(_call_event_helper, env, getattr(extract_context, name), values["ranges"])
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
