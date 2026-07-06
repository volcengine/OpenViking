# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Restricted Python trigger validation and evaluation.

The trigger contract is intentionally narrow:

    def should_trigger(ctx):
        return ctx.get("candidate_tool") == "refund_order"

The sandbox validates AST shape before compiling, exposes a tiny builtins set,
and evaluates with a wall-clock timeout.  Timeouts and runtime errors are
reported as non-triggering results by callers.
"""

from __future__ import annotations

import ast
import re
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable


class TriggerSandboxError(Exception):
    """Base error for trigger validation/evaluation."""


class TriggerValidationError(TriggerSandboxError):
    """Raised when trigger code violates the restricted Python contract."""


@dataclass(slots=True)
class _CompiledTrigger:
    function: Callable[[dict[str, Any]], Any]


def _regex_search(pattern: str, text: Any) -> bool:
    try:
        return re.search(str(pattern), str(text or ""), flags=re.IGNORECASE) is not None
    except re.error:
        return False


def _regex_match(pattern: str, text: Any) -> bool:
    try:
        return re.match(str(pattern), str(text or ""), flags=re.IGNORECASE) is not None
    except re.error:
        return False


_ALLOWED_BUILTINS: dict[str, Any] = {
    "any": any,
    "all": all,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "regex_match": _regex_match,
    "regex_search": _regex_search,
    "reversed": reversed,
    "set": set,
    "str": str,
    "sum": sum,
    "tuple": tuple,
}

_ALLOWED_METHODS = {
    "casefold",
    "count",
    "endswith",
    "find",
    "get",
    "index",
    "isalnum",
    "isalpha",
    "isdigit",
    "items",
    "keys",
    "lower",
    "replace",
    "rfind",
    "rindex",
    "split",
    "startswith",
    "strip",
    "upper",
    "values",
}

_FORBIDDEN_NAMES = {
    "__builtins__",
    "__class__",
    "__dict__",
    "__globals__",
    "__import__",
    "__mro__",
    "__subclasses__",
    "compile",
    "eval",
    "exec",
    "globals",
    "locals",
    "open",
    "print",
}

_FORBIDDEN_NODE_TYPES = (
    ast.AsyncFor,
    ast.AsyncFunctionDef,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.Global,
    ast.Import,
    ast.ImportFrom,
    ast.Lambda,
    ast.Nonlocal,
    ast.Raise,
    ast.Try,
    ast.TryStar,
    ast.While,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)


class _TriggerValidator(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> Any:  # noqa: ANN401
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            raise TriggerValidationError(f"forbidden syntax: {type(node).__name__}")
        return super().visit(node)

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        functions = [stmt for stmt in node.body if isinstance(stmt, ast.FunctionDef)]
        if len(functions) != 1 or len(node.body) != 1:
            raise TriggerValidationError("trigger code must define exactly one function")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if getattr(self, "_inside_function", False):
            raise TriggerValidationError("nested functions are not allowed")
        self._inside_function = True
        if node.name != "should_trigger":
            raise TriggerValidationError("trigger function must be named should_trigger")
        args = node.args
        if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults:
            raise TriggerValidationError(
                "should_trigger must accept exactly one positional ctx arg"
            )
        if len(args.args) != 1 or args.args[0].arg != "ctx":
            raise TriggerValidationError("should_trigger signature must be should_trigger(ctx)")
        self.generic_visit(node)
        self._inside_function = False

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, int) and abs(node.value) > 100000:
            raise TriggerValidationError("integer constants are too large")
        if isinstance(node.value, str) and len(node.value) > 10000:
            raise TriggerValidationError("string constants are too large")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id.startswith("__") or node.id in _FORBIDDEN_NAMES:
            raise TriggerValidationError(f"forbidden name: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr.startswith("__"):
            raise TriggerValidationError(f"forbidden attribute: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Name):
            if func.id not in _ALLOWED_BUILTINS:
                raise TriggerValidationError(f"forbidden call: {func.id}")
        elif isinstance(func, ast.Attribute):
            if func.attr not in _ALLOWED_METHODS:
                raise TriggerValidationError(f"forbidden method call: {func.attr}")
        else:
            raise TriggerValidationError("unsupported call target")
        self.generic_visit(node)


def validate_trigger_code(code: str) -> None:
    """Validate trigger code against the restricted AST contract."""

    tree = _parse(code)
    _TriggerValidator().visit(tree)
    compile(tree, "<experience-trigger>", "exec")


def smoke_test_trigger_code(
    code: str,
    ctx: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 0.05,
) -> None:
    """Run a small preflight execution and require a bool return value."""

    compiled = _compile_trigger(code)
    smoke_ctx = ctx or {"messages": [], "candidate_tool": "", "candidate_tool_args": {}}
    try:
        result = _run_trigger(compiled.function, smoke_ctx, timeout_seconds=timeout_seconds)
    except Exception as exc:
        raise TriggerValidationError(f"trigger smoke test failed: {exc}") from exc
    if not isinstance(result, bool):
        raise TriggerValidationError("trigger smoke test must return bool")


def evaluate_trigger_code(
    code: str,
    ctx: dict[str, Any],
    *,
    timeout_seconds: float = 0.05,
) -> bool:
    """Evaluate trigger code and return a strict bool result.

    Validation errors, runtime errors, non-bool return values, and timeouts are
    converted to ``False`` so bad experiences do not block tool execution.
    """

    try:
        compiled = _compile_trigger(code)
    except TriggerSandboxError:
        return False

    try:
        result = _run_trigger(compiled.function, ctx, timeout_seconds=timeout_seconds)
    except Exception:
        return False
    return result if isinstance(result, bool) else False


def _run_trigger(
    function: Callable[[dict[str, Any]], Any],
    ctx: dict[str, Any],
    *,
    timeout_seconds: float,
) -> Any:
    timeout_seconds = max(0.001, float(timeout_seconds))
    deadline = time.perf_counter() + timeout_seconds

    def trace_func(frame, event, arg):  # noqa: ANN001, ARG001
        if time.perf_counter() > deadline:
            raise TimeoutError("experience trigger timed out")
        return trace_func

    old_trace = sys.gettrace()
    sys.settrace(trace_func)
    try:
        return function(_readonly_json(ctx))
    finally:
        sys.settrace(old_trace)


def _compile_trigger(code: str) -> _CompiledTrigger:
    tree = _parse(code)
    _TriggerValidator().visit(tree)
    compiled = compile(tree, "<experience-trigger>", "exec")
    globals_dict = {"__builtins__": MappingProxyType(_ALLOWED_BUILTINS)}
    locals_dict: dict[str, Any] = {}
    exec(compiled, globals_dict, locals_dict)
    function = locals_dict.get("should_trigger")
    if not callable(function):
        raise TriggerValidationError("should_trigger is not callable")
    return _CompiledTrigger(function=function)


def _parse(code: str) -> ast.Module:
    if not isinstance(code, str) or not code.strip():
        raise TriggerValidationError("trigger code is empty")
    try:
        return ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise TriggerValidationError(str(exc)) from exc


def _readonly_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return MappingProxyType({str(k): _readonly_json(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_readonly_json(v) for v in value)
    return str(value)
