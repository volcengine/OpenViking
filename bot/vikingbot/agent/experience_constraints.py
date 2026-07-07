"""VikingBot-local runtime for conditional experience constraints."""

from __future__ import annotations

import ast
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ConstraintExperience:
    uri: str
    name: str
    constraint: str
    trigger_code: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_content_and_fields(
        cls,
        content: str,
        *,
        uri: str,
        fallback_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ConstraintExperience | None":
        uri = str(uri or "").strip()
        if not uri:
            return None
        merged_metadata = _mapping(metadata)
        parsed = _parse_rendered_experience_trigger(content)
        trigger_code = str(
            merged_metadata.get("trigger_code") or parsed.get("trigger_code") or ""
        ).strip()
        constraint = str(
            merged_metadata.get("constraint")
            or merged_metadata.get("content")
            or parsed.get("constraint")
            or content
            or ""
        ).strip()
        if not trigger_code or not constraint:
            return None
        name = str(
            merged_metadata.get("experience_name")
            or merged_metadata.get("name")
            or parsed.get("experience_name")
            or fallback_name
            or uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        )
        return cls(
            uri=uri,
            name=name,
            constraint=constraint,
            trigger_code=trigger_code,
            metadata=merged_metadata,
        )


@dataclass(slots=True)
class ConstraintActivationInput:
    messages: list[Any]
    candidate_tool: str
    candidate_tool_args: Mapping[str, Any] | None
    experiences: Iterable[Any]
    reminded_exp_uris: set[str] = field(default_factory=set)
    timeout_seconds: float = 0.05


@dataclass(slots=True)
class ConstraintActivationResult:
    reminded: bool
    messages: list[Any]
    experience_uri: str | None = None
    experience_name: str | None = None
    reminder_message: dict[str, str] | None = None
    experience_uris: list[str] = field(default_factory=list)
    experience_names: list[str] = field(default_factory=list)
    reminder_messages: list[dict[str, str]] = field(default_factory=list)
    triggered_uris: list[str] = field(default_factory=list)
    event: dict[str, Any] | None = None


def apply_experience_constraint_reminder(
    activation_input: ConstraintActivationInput,
) -> ConstraintActivationResult:
    original_messages = list(activation_input.messages or [])
    ctx = build_trigger_context(
        messages=original_messages,
        candidate_tool=activation_input.candidate_tool,
        candidate_tool_args=activation_input.candidate_tool_args,
    )
    triggered = select_triggered_experiences(
        experiences=activation_input.experiences,
        ctx=ctx,
        reminded_exp_uris=activation_input.reminded_exp_uris,
        timeout_seconds=activation_input.timeout_seconds,
    )
    triggered_uris = [exp.uri for exp in triggered]
    if not triggered:
        return ConstraintActivationResult(
            reminded=False,
            messages=original_messages,
            triggered_uris=triggered_uris,
        )

    reminders = [
        render_reminder_message(exp, candidate_tool=activation_input.candidate_tool)
        for exp in triggered
    ]
    for exp in triggered:
        activation_input.reminded_exp_uris.add(exp.uri)
    event = {
        "type": "experience_constraint_reminder",
        "experience_uris": triggered_uris,
        "experience_names": [exp.name for exp in triggered],
        "candidate_tool": str(activation_input.candidate_tool or ""),
        "triggered_uris": triggered_uris,
    }
    first = triggered[0]
    return ConstraintActivationResult(
        reminded=True,
        messages=[*original_messages, *reminders],
        experience_uri=first.uri,
        experience_name=first.name,
        reminder_message=reminders[0],
        experience_uris=triggered_uris,
        experience_names=[exp.name for exp in triggered],
        reminder_messages=reminders,
        triggered_uris=triggered_uris,
        event=event,
    )


def select_triggered_experiences(
    *,
    experiences: Iterable[Any],
    ctx: dict[str, Any],
    reminded_exp_uris: set[str] | None = None,
    timeout_seconds: float = 0.05,
) -> list[ConstraintExperience]:
    reminded = set(reminded_exp_uris or set())
    triggered: list[ConstraintExperience] = []
    for exp in experiences or []:
        if not isinstance(exp, ConstraintExperience) or exp.uri in reminded:
            continue
        try:
            validate_trigger_code(exp.trigger_code)
        except Exception as exc:
            logger.warning(
                "[EXP_CONSTRAINT_TRIGGER]: invalid trigger uri=%s name=%s error=%s",
                exp.uri,
                exp.name,
                exc,
            )
            continue
        if evaluate_trigger_code(exp.trigger_code, ctx, timeout_seconds=timeout_seconds):
            triggered.append(exp)
    return triggered


def build_trigger_context(
    *,
    messages: list[Any],
    candidate_tool: str,
    candidate_tool_args: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "messages": sanitize_messages(messages),
        "candidate_tool": str(candidate_tool or ""),
        "candidate_tool_args": _json_safe(dict(candidate_tool_args or {})),
    }


def sanitize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for message in messages or []:
        if isinstance(message, Mapping):
            item: dict[str, Any] = {
                "role": str(message.get("role", "") or ""),
                "content": _safe_content(message.get("content")),
            }
            if message.get("name"):
                item["name"] = str(message.get("name"))
            if message.get("tool_call_id"):
                item["tool_call_id"] = str(message.get("tool_call_id"))
            if message.get("tool_calls"):
                item["tool_calls"] = _json_safe(message.get("tool_calls"))
            sanitized.append(item)
            continue

        role = str(getattr(message, "role", "") or "")
        content = str(getattr(message, "content", "") or "")
        sanitized.append({"role": role, "content": content})
    return sanitized


def _render_structured_experience_reminder(
    experience: ConstraintExperience,
    *,
    candidate_tool: str,
) -> str:
    tool_name = str(candidate_tool or "某个工具/方法").strip() or "某个工具/方法"
    return (
        "<experience_reminder>\n"
        f"<experience_name>{experience.name}</experience_name>\n"
        f"<experience_uri>{experience.uri}</experience_uri>\n"
        f"<triggered_before_tool>{tool_name}</triggered_before_tool>\n"
        "<instruction>\n"
        "下面是一条经验 reminder，它是在你可能要调用上述工具/方法前被触发的。\n"
        "请先参考这段经验，再决定下一步是否以及如何调用该工具/方法。\n"
        "当前系统规则、用户事实和工具结果优先于这段经验。\n"
        "</instruction>\n"
        "<experience>\n"
        f"{experience.constraint}\n"
        "</experience>\n"
        "</experience_reminder>"
    )


def render_reminder_message(
    experience: ConstraintExperience,
    *,
    candidate_tool: str,
) -> dict[str, str]:
    return {
        "role": "user",
        "content": _render_structured_experience_reminder(
            experience,
            candidate_tool=candidate_tool,
        ),
    }


def evaluate_trigger_code(
    code: str,
    ctx: dict[str, Any],
    *,
    timeout_seconds: float = 0.05,
) -> bool:
    try:
        compiled = _compile_trigger(code)
        result = _run_trigger(compiled.function, ctx, timeout_seconds=timeout_seconds)
    except Exception:
        return False
    return result if isinstance(result, bool) else False


def validate_trigger_code(code: str) -> None:
    """Validate trigger code against VikingBot's constraint runtime contract."""

    _compile_trigger(code)


def smoke_test_trigger_code(
    code: str,
    *,
    smoke_contexts: Iterable[dict[str, Any]] | None = None,
    timeout_seconds: float = 0.05,
) -> None:
    """Validate that trigger code returns bool on representative read-only contexts."""

    compiled = _compile_trigger(code)
    contexts = list(smoke_contexts or _default_smoke_contexts())
    for ctx in contexts:
        try:
            result = _run_trigger(compiled.function, ctx, timeout_seconds=timeout_seconds)
        except Exception as exc:
            raise _TriggerValidationError(f"trigger smoke test failed: {exc}") from exc
        if not isinstance(result, bool):
            raise _TriggerValidationError("trigger smoke test must return bool")


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
    "count",
    "endswith",
    "casefold",
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


class _TriggerValidationError(Exception):
    pass


class _TriggerValidator(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> Any:  # noqa: ANN401
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            raise _TriggerValidationError(f"forbidden syntax: {type(node).__name__}")
        return super().visit(node)

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        functions = [stmt for stmt in node.body if isinstance(stmt, ast.FunctionDef)]
        if len(functions) != 1 or len(node.body) != 1:
            raise _TriggerValidationError("trigger code must define exactly one function")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if getattr(self, "_inside_function", False):
            raise _TriggerValidationError("nested functions are not allowed")
        self._inside_function = True
        if node.name != "should_trigger":
            raise _TriggerValidationError("trigger function must be named should_trigger")
        args = node.args
        if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults:
            raise _TriggerValidationError(
                "should_trigger must accept exactly one positional ctx arg"
            )
        if len(args.args) != 1 or args.args[0].arg != "ctx":
            raise _TriggerValidationError("should_trigger signature must be should_trigger(ctx)")
        self.generic_visit(node)
        self._inside_function = False

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, int) and abs(node.value) > 100000:
            raise _TriggerValidationError("integer constants are too large")
        if isinstance(node.value, str) and len(node.value) > 10000:
            raise _TriggerValidationError("string constants are too large")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id.startswith("__") or node.id in _FORBIDDEN_NAMES:
            raise _TriggerValidationError(f"forbidden name: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr.startswith("__"):
            raise _TriggerValidationError(f"forbidden attribute: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Name):
            if func.id not in _ALLOWED_BUILTINS:
                raise _TriggerValidationError(f"forbidden call: {func.id}")
        elif isinstance(func, ast.Attribute):
            if func.attr not in _ALLOWED_METHODS:
                raise _TriggerValidationError(f"forbidden method call: {func.attr}")
        else:
            raise _TriggerValidationError("unsupported call target")
        self.generic_visit(node)


def _compile_trigger(code: str) -> _CompiledTrigger:
    if not isinstance(code, str) or not code.strip():
        raise _TriggerValidationError("trigger code is empty")
    tree = ast.parse(code, mode="exec")
    _TriggerValidator().visit(tree)
    compiled = compile(tree, "<vikingbot-experience-trigger>", "exec")
    globals_dict = {"__builtins__": MappingProxyType(_ALLOWED_BUILTINS)}
    locals_dict: dict[str, Any] = {}
    exec(compiled, globals_dict, locals_dict)
    function = locals_dict.get("should_trigger")
    if not callable(function):
        raise _TriggerValidationError("should_trigger is not callable")
    return _CompiledTrigger(function=function)


def _default_smoke_contexts() -> list[dict[str, Any]]:
    return [
        {
            "messages": [],
            "candidate_tool": "",
            "candidate_tool_args": {},
        },
        {
            "messages": [{"role": "user", "content": "smoke test message"}],
            "candidate_tool": "communicate_with_user",
            "candidate_tool_args": {"content": "smoke test content"},
        },
    ]


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


class _ReadOnlyDict(dict):
    def _readonly(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("trigger context is read-only")

    __setitem__ = _readonly
    __delitem__ = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    update = _readonly
    __ior__ = _readonly


class _ReadOnlyList(list):
    def _readonly(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("trigger context is read-only")

    __setitem__ = _readonly
    __delitem__ = _readonly
    append = _readonly
    clear = _readonly
    extend = _readonly
    insert = _readonly
    pop = _readonly
    remove = _readonly
    reverse = _readonly
    sort = _readonly
    __iadd__ = _readonly
    __imul__ = _readonly


def _readonly_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return _ReadOnlyDict({str(k): _readonly_json(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return _ReadOnlyList([_readonly_json(v) for v in value])
    return str(value)


def _parse_rendered_experience_trigger(content: str) -> dict[str, str]:
    text = str(content or "")
    section_match = re.search(
        r"(?ims)^#{1,6}\s*Experience\s+Trigger\s*\n(?P<section>.*?)(?=^#{1,6}\s+|\Z)",
        text,
    )
    if not section_match:
        return {}

    section = section_match.group("section")
    parsed: dict[str, str] = {}
    name_match = re.search(r"(?im)^\s*-?\s*experience_name\s*:\s*(?P<name>[^\n]+)", section)
    if name_match:
        parsed["experience_name"] = name_match.group("name").strip().strip("` ")
    trigger_match = re.search(
        r"(?is)trigger_code\s*:\s*```(?:python)?\s*(?P<code>.*?)\s*```",
        section,
    )
    if trigger_match:
        parsed["trigger_code"] = trigger_match.group("code").strip()

    constraint = (text[: section_match.start()] + text[section_match.end() :]).strip()
    if constraint:
        parsed["constraint"] = constraint
    return parsed


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(_json_safe(value))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)
