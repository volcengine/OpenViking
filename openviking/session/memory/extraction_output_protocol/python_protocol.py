# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Restricted Python SDK output protocol for memory extraction."""

from __future__ import annotations

import ast
import json
import keyword
import re
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from openviking.core.peer_id import safe_peer_id
from openviking.session.memory.dataclass import MemoryFile, MemoryTypeSchema
from openviking.session.memory.extraction_output_protocol.base import (
    ExtractionOutputContext,
    ExtractionOutputProtocol,
    ExtractionOutputProtocolError,
)
from openviking.session.memory.merge_op import (
    DeleteBlock,
    FieldType,
    MergeOp,
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.utils.line_numbers import (
    every_line_has_line_numbers,
    strip_line_numbers,
)

_PYTHON_FENCE_RE = re.compile(r"```python[ \t]*\r?\n(?P<code>[\s\S]*?)```", re.IGNORECASE)
_PYTHON_FENCE_START_RE = re.compile(r"```python[ \t]*\r?\n", re.IGNORECASE)
_HIDDEN_MEMORY_FIELDS = {
    "source_extraction_id",
    "source_extraction_ids",
    "last_update_trace_id",
    "version",
}
_SAFE_BUILTINS = {
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "str": str,
    "tuple": tuple,
}
_SAFE_STRING_METHODS = {
    "endswith",
    "join",
    "lower",
    "replace",
    "split",
    "startswith",
    "strip",
    "upper",
}
# Upper bound on the size of a value produced by a DSL expression. Multiplying or
# joining a small literal by a huge count would otherwise allocate a massive
# string/list in the server process before schema validation runs; the check is
# applied to the would-be result size before any allocation.
_MAX_EXPRESSION_SIZE = 1_000_000
_REPEATABLE_SEQUENCES = (str, bytes, list, tuple)
_CONTRACT_PREAMBLE = (
    "## Output Format: restricted Python memory SDK",
    "Return only Python code, optionally wrapped in one ```python code fence.",
    "The code is interpreted as a restricted DSL and cannot access Python modules, files, or the network.",
    "The SDK is write-only. To inspect memories, call the native search/read tools before returning the program; never emit sdk.search(), sdk.read(), or sdk.existing() (only the system binds existing objects).",
    "Never return memory content as bare Markdown; all changes must be arguments to SDK calls.",
    "Existing objects shown by the system may use obj.update(field=value, ...) to set complete "
    "scalar/immutable fields, obj.delete(replacement=None),",
    "and, when links are enabled, obj.link(target, link_type='related_to', weight=0.5, match_text=None, description='').",
    "To change an existing string field, edit it through the object's field attribute. Use the "
    "real field name (e.g. content), NOT the literal word 'field':",
    "  - obj.content.update(new_value): replace the whole field with a complete new string.",
    "  - obj.content.edit(search=..., replace=...): replace one exact snippet in place.",
    "  - obj.content.drop(text=...): delete one exact snippet in place.",
    "edit()/drop() may be chained, e.g. obj.content.edit(search='a', replace='b').drop(text='c'); do not mix them with .update() in one chain.",
    "A field attribute is a write handle only; you cannot read it as a string or call str methods on it.",
    "Each search= (and drop text=) MUST be copied verbatim from the current field value shown in the object's sdk.existing(...) binding, and must occur exactly once. If the snippet appears more than once, include an adjacent unique line just before or after it so the match is unique. Never use text from the conversation or the new facts you intend to add as a search anchor; that text is not in the current content and the edit will fail.",
    "edit()/drop() only work on an existing memory's string field; new memories from create/set must be given complete field values.",
    "For existing memories, prefer the smallest unique edit()/drop(). Do not rewrite the entire field just to add or change a few facts; large full-content rewrites are more likely to be truncated or malformed. Use obj.content.update() only when most of the content changes.",
    'ALWAYS use a triple-quoted string ("""...""") for EVERY natural-language argument '
    "(content, summary, goal, and every search=/replace=/text= snippet), even one-liners. "
    "Prose frequently contains apostrophes (e.g. Evan's), quotes, colons, or dates that break "
    'single- or double-quoted literals. Inside triple quotes, escape any literal """ and '
    "backslash; never put a real newline inside a single- or double-quoted string.",
    "Only keyword arguments are accepted by create, set, and obj.update(); a field's update() takes one positional string. Unknown business fields are ignored.",
    "You may end the program with sdk.commit(); when present it must be the final call. Return an empty program when there are no changes.",
    "Use the system-provided existing-object variable names exactly as shown. When a newly "
    "created memory must be referenced by delete(replacement=...) or link(...), assign the "
    "create call to a variable first, for example: canonical = sdk.create_<type>(...); "
    "duplicate_1.delete(replacement=canonical). Never recreate or rebind an existing object "
    "with sdk.existing().",
    "",
    "Available write methods for this extraction:",
)

_LINK_RULES = (
    "",
    "## Link Rules",
    "- Create links between memories when they have a meaningful and clear relationship "
    "(e.g. an event mentioning a person/entity, a preference about a place/activity). "
    "Do NOT force links between unrelated items.",
    "- Use obj.link(target, link_type='related_to', match_text='<word>') as a standalone "
    "statement after both objects exist.",
    "- match_text must be a single exact word that appears in the source object's content. "
    "It is where the link anchor is inserted. Choose the most specific identifying word "
    "(e.g. a person's name, a place, an activity).",
    "- link_type uses lowercase snake_case: related_to (default), belongs_to, "
    "derived_from, caused_by, contradicts, evolved_from.",
    "- For a newly created object, assign the create/set call to a variable first, then "
    "call .link() on it (see the preamble); existing objects use their provided variable names.",
    "- Links are optional; only create them when the relationship is explicit in the "
    "conversation. Do not link every memory to every other memory.",
    "",
    "Example — linking a newly created memory to another new one and to an existing binding "
    "(replace create_<type>/fields with methods and fields from the contract above):",
    "```python",
    "obj_a = sdk.create_<type_a>(...)  # a new memory, assigned to a variable",
    "obj_b = sdk.create_<type_b>(...)  # another new memory",
    'obj_a.link(obj_b, link_type="related_to", match_text="<word_in_obj_a>")',
    'obj_a.link(existing_1, link_type="related_to", match_text="<word_in_obj_a>")',
    "```",
)


@dataclass(slots=True)
class _MemoryObject:
    name: str
    memory_type: str
    page_id: int
    fields: dict[str, Any]
    existing: bool
    deleted: bool = False
    replacement: _MemoryObject | None = None
    changed_fields: dict[str, Any] = field(default_factory=dict)


_UNSET = object()


@dataclass(slots=True)
class _FieldHandle:
    """A pending edit chain on one field of a memory object (obj.field.*)."""

    owner: _MemoryObject
    field_name: str
    blocks: list[Any] = field(default_factory=list)
    full_value: Any = _UNSET


class PythonExtractionOutputProtocol(ExtractionOutputProtocol):
    """Restricted Python-shaped memory SDK compiler."""

    name = "python"

    def __init__(self) -> None:
        self._uri_to_name: dict[str, str] = {}
        self._type_counts: dict[str, int] = {}
        self._context_counts: dict[str, int] = {}
        self._last_error_allows_tool_retry = False

    def render_contract(self, context: ExtractionOutputContext) -> str:
        lines = list(_CONTRACT_PREAMBLE)
        for schema in context.schemas:
            lines.extend(self._render_schema_contract(context, schema))
        if not context.link_enabled:
            lines.append("Links are disabled; obj.link(...) is unavailable.")
        else:
            lines.extend(_LINK_RULES)
        return "\n".join(lines)

    def _render_schema_contract(
        self, context: ExtractionOutputContext, schema: MemoryTypeSchema
    ) -> list[str]:
        fields = _protocol_fields(context, schema)
        type_alias = _identifier_alias(schema.memory_type)
        signature = ", ".join(
            f"{_identifier_alias(name)}: {type_name}" for name, type_name, _description in fields
        )
        verb = "create" if schema.filename_has_variables() else "set"
        identity_fields = _model_visible_identity_fields(context, schema)
        identity_label = (
            ", ".join(_identifier_alias(name) for name in identity_fields)
            or "target scope (fixed to self)"
        )
        lines = [
            f"- sdk.{verb}_{type_alias}(*, {signature})",
            f"  - Identity fields (primary key): {identity_label}. Calls with identical "
            "identity field values address the same memory object.",
        ]
        operation_field = context.operations_model.model_fields.get(schema.memory_type)
        schema_description = getattr(operation_field, "description", None)
        if schema_description:
            # Reuse the already-rendered description from SchemaModelGenerator.
            # This keeps routing/cleanup rules identical to the JSON schema contract
            # (and resolves template variables such as {{ language }}).
            lines.append("  - Memory type rules:\n" + str(schema_description).strip())
        if verb == "set":
            lines.extend(self._render_singleton_rules(context, schema, identity_fields))
        merge_ops = {
            field.name: (
                "editable string: obj.field.edit/drop/update"
                if field.merge_op == MergeOp.PATCH and field.field_type == FieldType.STRING
                else field.merge_op.value
            )
            for field in schema.fields
        }
        for name, _type_name, description in fields:
            normalized_description = " ".join(str(description or "").split())
            qualifier = f" [{merge_ops[name]}]" if name in merge_ops else ""
            lines.append(f"  - {_identifier_alias(name)}{qualifier}: {normalized_description}")
        return lines

    @staticmethod
    def _render_singleton_rules(
        context: ExtractionOutputContext,
        schema: MemoryTypeSchema,
        identity_fields: tuple[str, ...],
    ) -> list[str]:
        type_alias = _identifier_alias(schema.memory_type)
        lines = [
            f"  - {type_alias} is a SINGLETON per identity: sdk.create_{type_alias}() "
            f"does not exist, and sdk.set_{type_alias}() must appear AT MOST ONCE for each "
            "identity in the whole program. Never call it twice for the same identity (e.g. the same "
            "peer_id). If several people or sections belong to one identity, put them all as separate "
            "H1 sections inside a SINGLE content argument of one call — different headings or person "
            "names do NOT create separate identities. To change an already-shown existing object, "
            "edit that object instead of calling set again."
        ]
        if "peer_id" in identity_fields and context.role_scope is not None:
            if context.role_scope.user_ids:
                lines.append(
                    f"  - Current self identity: exactly one sdk.set_{type_alias}() call "
                    "without peer_id; combine all content sections into its single content argument."
                )
            for peer_id in context.role_scope.peer_ids:
                lines.append(
                    f"  - Current identity peer_id={peer_id!r}: exactly one "
                    f"sdk.set_{type_alias}(peer_id={peer_id!r}, ...) call; combine every "
                    "person and section for this peer_id into that one content argument."
                )
        return lines

    def render_reference_rules(self, context: ExtractionOutputContext) -> str:
        del context
        return """
## Memory Object Rules
- Update or delete an existing memory only through its system-provided bound object; never pass or construct a URI.
- Create collection memories with listed sdk.create_<memory_type>(...) methods.
- Set a single-file memory with its listed sdk.set_<memory_type>(...) method; each target scope has only one such object.
- Existing-object identity, storage paths, and immutable fields are preserved by the system.
- delete() removes the whole object; use obj.content.drop(text=...) (with the real field name) when only some content must go and the rest stays.
- For canonical merges, use duplicate.delete(replacement=canonical); for pure deletes, call delete() without replacement.
- delete(replacement=canonical) discards the duplicate's content entirely and keeps only the canonical. Before deleting a duplicate, first fold every distinct valid fact it holds into the canonical (e.g. canonical.content.edit(...)); merging or compacting must never drop a unique fact that only the duplicate recorded.
"""

    def parse(
        self, content: str, context: ExtractionOutputContext
    ) -> tuple[Any | None, str | None]:
        self._last_error_allows_tool_retry = False
        try:
            code = _extract_python_code(content)
            compiler = _PythonProgramCompiler(context=context, protocol=self)
            operations = compiler.compile(code)
            return operations, None
        except ExtractionOutputProtocolError as exc:
            self._last_error_allows_tool_retry = exc.allow_tool_retry
            return None, str(exc)
        except Exception as exc:
            return None, str(exc)

    def render_final_instruction(self, context: ExtractionOutputContext) -> str:
        del context
        return (
            "You have reached the maximum number of tool call iterations. Do not call any more "
            "tools. Return the complete restricted Python memory SDK program now. Output only "
            "Python code. If there are no changes, return an empty program."
        )

    def render_format_retry(self, error: str | None = None) -> str:
        detail = f" Parser error: {error}" if error else ""
        tool_guidance = ""
        binding_guidance = ""
        quote_guidance = ""
        if error and "sdk.existing() is reserved" in error:
            binding_guidance = (
                " Use the existing-object variable names already supplied by the system; do not "
                "emit sdk.existing(). If a new object is the replacement, assign its create call "
                "first, for example `canonical = sdk.create_<type>(...)`, then call "
                "`duplicate_1.delete(replacement=canonical)`."
            )
        if error and _is_string_literal_syntax_error(error):
            quote_guidance = (
                " If the error is about a string literal, the offending line is shown above; a "
                "quote or newline inside prose likely ended the string early. Rewrite every "
                "natural-language argument (content, summary, goal, and every search=/replace=/"
                'text= snippet) as a triple-quoted string ("""...""") so embedded quotes and '
                "newlines stay inside the literal."
            )
        if self.keep_tools_enabled_after_parse_error(error):
            tool_guidance = (
                " The memory SDK is write-only. Call the available native search/read tool now; "
                "after its result is returned, update the system-provided object variable. "
                "Do not emit sdk.search(), sdk.read(), or construct a memory URI in Python."
            )
        return (
            "Your previous output was not a valid restricted Python memory SDK program."
            f"{detail}{tool_guidance}{binding_guidance}{quote_guidance} Regenerate the complete "
            "program and output no explanation. "
            "Put Markdown memory content inside quoted SDK arguments, using triple-quoted strings "
            '("""...""") for any multi-line or Markdown text so newlines and quotes stay inside '
            "the literal; never return it as the program itself. No changes from the invalid program "
            "were applied."
        )

    def keep_tools_enabled_after_parse_error(self, error: str | None) -> bool:
        # Set by parse() from the structured error flag, so this stays correct
        # even if the write-only read/search rejection wording changes.
        del error
        return self._last_error_allows_tool_retry

    def describe_empty_response(self) -> str | None:
        return "The model returned an empty Python program"

    def render_patch_repair(self, patch_errors: list[dict[str, Any]]) -> str:
        details = json.dumps(patch_errors, ensure_ascii=False, indent=2)
        return (
            "An obj.field.edit()/drop() change could not be applied to the target memory object. "
            "The search= (and drop text=) must be copied verbatim from the current field value shown "
            "in that object's sdk.existing(...) binding, and must occur exactly once. If it occurs "
            "more than once, include enough contiguous surrounding context to make it unique. Do not "
            "use text from the conversation or the new facts you intend to add as a search anchor. "
            "If you copy from numbered read output, exclude the `line_number<TAB>` prefix. If the "
            "change is too broad for a clean snippet, use obj.field.update(complete_new_value) instead. "
            "Regenerate the complete restricted Python SDK program, including previous successful "
            "operations and fixed failed operations. Output only Python code."
            "\n\nFailed edits:\n" + details
        )

    def render_resolution_repair(self, issues: list[dict[str, Any]]) -> str:
        details = json.dumps(issues, ensure_ascii=False, indent=2)
        return (
            "Some sdk.create_events(...) calls could not resolve a safe write target. "
            "Re-emit ONLY corrected create_events() calls for the failed items below; do not "
            "emit any other memory type, delete, or link statements. The server has preserved "
            "all successful operations from the previous program. "
            "For event ranges, pass ranges= with valid in-bounds message indexes that include the "
            "user-role message establishing the event so its owner can be resolved (e.g. "
            'ranges="3-5"). Do not target a disallowed or ambiguous peer. Keep the same event_name '
            "shown for each failed item so it maps back to that event. Use triple-quoted strings "
            '("""...""") for every natural-language argument. Output only Python code.'
            "\n\nResolution issues:\n" + details
        )

    def render_new_bindings(self, context: ExtractionOutputContext, *, source: str) -> str:
        declarations = []
        for uri, memory_file in context.read_file_contents.items():
            declaration = self._render_existing_binding(
                context, uri=uri, memory_file=memory_file, result=None, source=source
            )
            if declaration:
                declarations.append(declaration)
        if not declarations:
            return ""
        return "\n".join(declarations)

    def render_tool_result_messages(
        self,
        context: ExtractionOutputContext,
        *,
        call_id: str | int,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        source: str,
    ) -> list[dict[str, Any]]:
        del call_id
        code = self._render_tool_result(
            context, tool_name=tool_name, params=params, result=result, source=source
        )
        return [{"role": "user", "content": code}]

    def render_prefetch_messages(
        self,
        messages: list[dict[str, Any]],
        context: ExtractionOutputContext,
    ) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        for message in messages:
            invocation = _parse_tool_result_message(message)
            if invocation is None:
                # Provider-authored conversation/instruction text may still name
                # JSON; adapt its wording to this protocol without touching the
                # structured tool-result messages rendered below.
                rendered.append(self._normalize_message_instruction(message))
                continue
            tool_name, params, result = invocation
            rendered.extend(
                self.render_tool_result_messages(
                    context,
                    call_id="prefetch",
                    tool_name=tool_name,
                    params=params,
                    result=result,
                    source="prefetch",
                )
            )

        remaining_bindings = self.render_new_bindings(context, source="prefetch")
        if remaining_bindings:
            rendered.append({"role": "user", "content": remaining_bindings})
        return rendered

    def _normalize_message_instruction(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content")
        if not isinstance(content, str):
            return message
        normalized = self.normalize_provider_instruction(content)
        if normalized == content:
            return message
        return {**message, "content": normalized}

    def _render_tool_result(
        self,
        context: ExtractionOutputContext,
        *,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        source: str,
    ) -> str:
        if tool_name == "read" and isinstance(result, dict) and "error" not in result:
            uri = str(params.get("uri") or result.get("uri") or "")
            memory_file = context.read_file_contents.get(uri)
            if memory_file is not None:
                binding = self._render_existing_binding(
                    context,
                    uri=uri,
                    memory_file=memory_file,
                    result=result,
                    source=source,
                )
                if binding:
                    return binding
            return self._render_reference_context(result, preferred_name=result.get("context_role"))

        if tool_name == "search":
            return self._render_search_comment(context, params, result)

        if isinstance(result, dict) and "error" in result:
            name = self._next_context_name(f"{tool_name}_error")
            return f"# {tool_name} failed for {params!r}.\n{name} = {result!r}"

        name = self._next_context_name(f"{tool_name}_result")
        return f"# {tool_name} result loaded by {source}.\n{name} = {result!r}"

    @staticmethod
    def _render_search_comment(
        context: ExtractionOutputContext, params: dict[str, Any], result: Any
    ) -> str:
        # Search only returns URIs (no content), so it cannot become an
        # sdk.existing() binding. Files actually read are bound separately as
        # sdk.existing(); how to reach the rest depends on whether a read tool
        # is available this run.
        uris = _search_result_uris(result)
        query = params.get("query")
        query_label = f" query={query!r}" if query else ""
        if not uris:
            return f"# Search{query_label} found no memory files."
        listing = "\n".join(f"# - {uri}" for uri in uris)
        if "read" in context.available_tools:
            guidance = "read them with the read tool before updating them"
        else:
            guidance = (
                "any you need to edit are already provided as sdk.existing bindings; "
                "there is no read tool, so treat anything not shown above as new"
            )
        return f"# Search{query_label} found the following memory files; {guidance}:\n{listing}"

    def _render_existing_binding(
        self,
        context: ExtractionOutputContext,
        *,
        uri: str,
        memory_file: MemoryFile,
        result: dict[str, Any] | None,
        source: str,
    ) -> str:
        name = self._uri_to_name.get(uri)
        if name is not None and result is None:
            return ""
        memory_type = str(
            memory_file.memory_type or memory_file.extra_fields.get("memory_type") or "memory"
        )
        schemas = {schema.memory_type: schema for schema in context.schemas}
        schema = schemas.get(memory_type)
        if schema is None:
            return ""

        if name is None:
            count = self._type_counts.get(memory_type, 0) + 1
            self._type_counts[memory_type] = count
            # Binding variable names must be valid Python identifiers, so alias
            # the memory_type on the DSL surface (real name is kept everywhere else).
            name = f"{_identifier_alias(memory_type)}_{count}"
            self._uri_to_name[uri] = name
        fields = _visible_memory_fields(memory_file, schema, context)
        maintenance_notice = None
        if result is not None:
            fields.update({key: result[key] for key in fields.keys() & result.keys()})
            maintenance_notice = result.get("memory_maintenance_notice")
        content = fields.get("content")
        if isinstance(content, str) and every_line_has_line_numbers(content):
            fields["content"] = strip_line_numbers(content)
        args = [f"memory_type={memory_type!r}"]
        args.extend(f"{_identifier_alias(key)}={value!r}" for key, value in fields.items())
        binding = (
            f"# Existing memory loaded by {source}; this binding is system-provided.\n"
            f"{name} = sdk.existing({', '.join(args)})"
        )
        if maintenance_notice is not None:
            binding += "\n# Memory maintenance notice: " + json.dumps(
                maintenance_notice, ensure_ascii=False
            )
        return binding

    def _render_reference_context(self, result: Any, *, preferred_name: Any = None) -> str:
        name = self._next_context_name(str(preferred_name or "read_result"))
        return f"# Read-only context; this is not a mutable memory object.\n{name} = {result!r}"

    def _next_context_name(self, stem: str) -> str:
        normalized = re.sub(r"\W+", "_", stem).strip("_").lower() or "context"
        if normalized[0].isdigit() or keyword.iskeyword(normalized):
            normalized = f"context_{normalized}"
        count = self._context_counts.get(normalized, 0) + 1
        self._context_counts[normalized] = count
        return normalized if count == 1 else f"{normalized}_{count}"

    def normalize_provider_instruction(self, instruction: str) -> str:
        replacements = {
            "Output JSON only. Do not call any tools.": "Do not call any tools. Follow the output contract below.",
            "Do not call tools. Output JSON only.": "Do not call tools. Follow the output contract below.",
            "follow the provided JSON schema": "follow the provided output contract",
            "Output JSON only.": "Follow the output contract below.",
            "output ONLY a JSON object (no extra text before or after)": "follow the output contract below",
            "output only JSON that matches the schema descriptions": "follow the output contract below",
            "Do NOT use `delete_ids`": "Do NOT call `delete()`",
            "put it in delete_ids": "delete it through its bound memory object",
        }
        for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            instruction = instruction.replace(old, new)
        return instruction

    def binding_name(self, uri: str) -> str | None:
        return self._uri_to_name.get(uri)


def _parse_tool_result_message(
    message: dict[str, Any],
) -> tuple[str, dict[str, Any], Any] | None:
    if message.get("role") != "user" or not isinstance(message.get("content"), str):
        return None
    try:
        payload = json.loads(message["content"])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_call_name"), str):
        return None
    params = payload.get("args")
    if not isinstance(params, dict) or "result" not in payload:
        return None
    return payload["tool_call_name"], params, payload["result"]


def _search_result_uris(result: Any) -> list[str]:
    """Extract memory-file URIs from a search tool result of varying shape."""
    items: Any
    if isinstance(result, dict):
        items = result.get("memories", [])
    elif isinstance(result, list):
        items = result
    else:
        items = []
    uris: list[str] = []
    for item in items:
        uri = item.get("uri") if isinstance(item, dict) else item
        if isinstance(uri, str) and uri:
            uris.append(uri)
    return list(dict.fromkeys(uris))


class _PythonProgramCompiler:
    def __init__(
        self, *, context: ExtractionOutputContext, protocol: PythonExtractionOutputProtocol
    ) -> None:
        self.context = context
        self.protocol = protocol
        self.schemas = {schema.memory_type: schema for schema in context.schemas}
        # DSL surface uses identifier aliases; map them back to real schema names.
        self._type_alias_to_real = {
            _identifier_alias(schema.memory_type): schema.memory_type
            for schema in context.schemas
        }
        self._field_alias_to_real = {
            schema.memory_type: {
                _identifier_alias(name): name
                for name, _type, _description in _protocol_fields(context, schema)
            }
            for schema in context.schemas
        }
        self.objects: dict[str, _MemoryObject] = {}
        self.values: dict[str, Any] = {}
        self.links: list[dict[str, Any]] = []
        self._singleton_identities: set[tuple[str, tuple[tuple[str, Any], ...]]] = set()
        self._next_new_page_id = self._find_next_new_page_id(100)
        self._committed = False
        self._load_existing_objects()

    def compile(self, code: str) -> Any:
        try:
            module = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            # A valid program that merely embeds Markdown in a triple-quoted
            # string parses fine, so only fall back to the bare-Markdown hint
            # when parsing actually fails.
            if _looks_like_bare_markdown(code):
                raise ExtractionOutputProtocolError(
                    "Output is bare Markdown, not a Python SDK program; put memory content inside "
                    "a quoted sdk.create_*/sdk.set_* argument or an existing object's update() call"
                ) from exc
            raise ExtractionOutputProtocolError(
                f"Line {exc.lineno or 1}: invalid Python syntax: {exc.msg}"
                + _format_syntax_error_source(exc)
            ) from exc
        # sdk.commit() is optional: an empty program (or one without commit) simply
        # means "no changes". When present, commit() still acts as a terminator and
        # must be the final statement.
        for index, statement in enumerate(module.body):
            if self._committed:
                self._error(statement, "sdk.commit() must be the final statement")
            self._execute_statement(statement)
            if self._committed and index != len(module.body) - 1:
                self._error(statement, "sdk.commit() must be the final statement")
        return self._build_operations()

    def _load_existing_objects(self) -> None:
        for uri, memory_file in self.context.read_file_contents.items():
            name = self.protocol.binding_name(uri)
            if not name:
                continue
            memory_type = str(
                memory_file.memory_type or memory_file.extra_fields.get("memory_type") or ""
            )
            if memory_type not in self.schemas:
                continue
            page_id = self.context.page_id_map.get_page_id(uri)
            self.objects[name] = _MemoryObject(
                name=name,
                memory_type=memory_type,
                page_id=page_id,
                fields=_visible_memory_fields(memory_file, self.schemas[memory_type], self.context),
                existing=True,
            )

    def _execute_statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                self._error(node, "only one simple assignment target is allowed")
            name = node.targets[0].id
            # Evaluate first so an attempted sdk.read()/sdk.search() reports the
            # actionable write-only SDK error even when the model reuses a
            # system-provided binding name on the left-hand side.
            value = self._eval(node.value)
            self._reserve_name(name, node)
            if value is None:
                self._error(node, "side-effect calls cannot be assigned")
            if isinstance(value, _MemoryObject):
                if value.existing or value.name:
                    self._error(
                        node, "only a new sdk.create_*() or sdk.set_*() result may be assigned"
                    )
                value.name = name
                self.objects[name] = value
            elif isinstance(value, _FieldHandle):
                self._error(
                    node, "field edits (obj.field.update/edit/drop) must be standalone statements"
                )
            else:
                if _contains_memory_object(value):
                    self._error(node, "memory objects cannot be stored in local containers")
                self.values[name] = value
            return
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            result = self._eval_call(node.value, statement=True)
            if isinstance(result, _MemoryObject):
                # An unassigned create/set is valid but intentionally cannot be referenced later.
                self.objects[f"__created_{result.page_id}"] = result
            elif isinstance(result, _FieldHandle):
                self._apply_field_handle(result, node)
            elif result is not None:
                self._error(node, "only SDK and memory-object calls may be standalone statements")
            return
        self._error(node, "only assignments and SDK/object calls are allowed")

    def _reserve_name(self, name: str, node: ast.AST) -> None:
        _require_identifier(name, "variable")
        if name == "sdk" or name in _SAFE_BUILTINS or name in self.objects or name in self.values:
            self._error(node, f"variable {name!r} is reserved or already assigned")

    def _eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.objects:
                return self.objects[node.id]
            if node.id in self.values:
                return self.values[node.id]
            self._error(node, f"unknown name {node.id!r}")
        if isinstance(node, ast.List):
            return [self._eval(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._eval(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            result = {}
            for key, value in zip(node.keys, node.values, strict=True):
                if key is None:
                    expanded = self._eval(value)
                    if not isinstance(expanded, dict):
                        self._error(node, "dictionary unpacking requires a dict")
                    result.update(expanded)
                else:
                    result[self._eval(key)] = self._eval(value)
            return result
        if isinstance(node, ast.Attribute):
            owner = self._eval(node.value)
            if not isinstance(owner, _MemoryObject):
                self._error(node, "attribute reads are only allowed on memory objects")
            # Field names use identifier aliases on the DSL surface; map back to
            # the real schema field name before resolving the write handle.
            alias_map = self._field_alias_to_real.get(owner.memory_type, {})
            real_field = alias_map.get(node.attr, node.attr)
            if node.attr.startswith("_") or real_field not in owner.fields:
                available = ", ".join(
                    sorted(_identifier_alias(name) for name in owner.fields)
                ) or "(none)"
                hint = (
                    " Use the real field name (e.g. content), not the literal word 'field'."
                    if node.attr == "field"
                    else ""
                )
                self._error(
                    node,
                    f"memory field {node.attr!r} is unavailable; editable fields on "
                    f"{owner.name or owner.memory_type}: {available}.{hint}",
                )
            # obj.<field> is a write handle, not the raw value: it exposes
            # .update()/.edit()/.drop() and cannot be read as a string.
            return _FieldHandle(owner=owner, field_name=real_field)
        if isinstance(node, ast.Subscript):
            return self._eval(node.value)[self._eval_slice(node.slice)]
        if isinstance(node, ast.JoinedStr):
            return "".join(
                self._eval_formatted_value(part)
                if isinstance(part, ast.FormattedValue)
                else part.value
                for part in node.values
            )
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node)
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            self._error(node, "unsupported unary operator")
        if isinstance(node, ast.BoolOp):
            value = self._eval(node.values[0])
            for operand in node.values[1:]:
                if isinstance(node.op, ast.And):
                    if not value:
                        return value
                elif value:
                    return value
                value = self._eval(operand)
            return value
        if isinstance(node, ast.Compare):
            return self._eval_compare(node)
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test) else node.orelse)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        self._error(node, f"unsupported expression {type(node).__name__}")

    def _eval_call(self, node: ast.Call, *, statement: bool = False) -> Any:
        if any(keyword.arg is None for keyword in node.keywords):
            self._error(node, "**kwargs is not allowed in calls")
        if isinstance(node.func, ast.Name):
            if node.func.id not in _SAFE_BUILTINS:
                self._error(
                    node,
                    f"function {node.func.id!r} is not allowed",
                    allow_tool_retry=node.func.id in ("read", "search"),
                )
            args = [self._eval(arg) for arg in node.args]
            kwargs = {item.arg: self._eval(item.value) for item in node.keywords}
            if _contains_memory_object((args, kwargs)):
                self._error(node, "memory objects cannot be converted or placed in containers")
            return _SAFE_BUILTINS[node.func.id](*args, **kwargs)
        if not isinstance(node.func, ast.Attribute):
            self._error(node, "unsupported call target")
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "sdk":
            return self._call_sdk(node, statement=statement)
        owner = self._eval(node.func.value)
        if isinstance(owner, _MemoryObject):
            return self._call_memory(owner, node.func.attr, node, statement=statement)
        if isinstance(owner, _FieldHandle):
            return self._call_field(owner, node.func.attr, node)
        if isinstance(owner, str) and node.func.attr in _SAFE_STRING_METHODS:
            args = [self._eval(arg) for arg in node.args]
            kwargs = {item.arg: self._eval(item.value) for item in node.keywords}
            if node.func.attr == "join" and args and isinstance(args[0], (list, tuple)):
                pieces = [str(piece) for piece in args[0]]
                projected = sum(len(piece.encode("utf-8")) for piece in pieces) + (
                    len(owner.encode("utf-8")) * max(len(pieces) - 1, 0)
                )
                if projected > _MAX_EXPRESSION_SIZE:
                    self._error(
                        node,
                        f"joined string would exceed the {_MAX_EXPRESSION_SIZE:,}-character size limit",
                    )
            return getattr(owner, node.func.attr)(*args, **kwargs)
        self._error(node, f"method {node.func.attr!r} is not allowed")

    def _call_sdk(self, node: ast.Call, *, statement: bool) -> Any:
        method = node.func.attr
        if method.startswith(("create_", "set_")):
            verb, type_alias = method.split("_", 1)
            if node.args:
                self._error(node, f"{verb} methods accept keyword arguments only")
            memory_type = self._type_alias_to_real.get(type_alias, type_alias)
            schema = self.schemas.get(memory_type)
            if schema is None:
                self._error(node, f"memory type {type_alias!r} is not available")
            expected_verb = "create" if schema.filename_has_variables() else "set"
            if verb != expected_verb:
                self._error(
                    node,
                    f"sdk.{method}() is unavailable for {type_alias!r}; "
                    f"use sdk.{expected_verb}_{type_alias}()",
                )
            allowed_fields = {
                name for name, _type, _description in _protocol_fields(self.context, schema)
            }
            kwargs = self._eval_keywords(
                node,
                allowed=allowed_fields,
                ignore_unknown=True,
                alias_map=self._field_alias_to_real.get(memory_type),
            )
            if _contains_memory_object(kwargs):
                self._error(node, "memory objects cannot be used as business field values")
            if any(isinstance(value, _FieldHandle) for value in kwargs.values()):
                action = "creating" if verb == "create" else "setting"
                self._error(
                    node,
                    f"field edits (obj.field.edit/drop) cannot be used when {action} a memory; "
                    "pass the complete field value",
                )
            missing_fields = [field.name for field in schema.fields if field.name not in kwargs]
            if missing_fields:
                self._error(
                    node,
                    f"{verb} requires complete memory fields; missing: "
                    + ", ".join(missing_fields),
                )
            if verb == "set":
                identity = _memory_identity(schema, kwargs)
                identity_key = (memory_type, identity)
                if identity_key in self._singleton_identities:
                    identity_text = _format_identity(identity)
                    peer_id = _normalized_peer_scope(kwargs.get("peer_id"))
                    target = f"peer {peer_id!r}" if peer_id is not None else "self"
                    self._error(
                        node,
                        f"duplicate {type_alias} identity ({identity_text}); "
                        f"sdk.set_{type_alias}() may be called only once for {target}; "
                        "combine all people and content sections for that target into "
                        "one content argument",
                    )
                self._singleton_identities.add(identity_key)
            obj = _MemoryObject(
                name="",
                memory_type=memory_type,
                page_id=self._next_new_page_id,
                fields=dict(kwargs),
                existing=False,
                changed_fields=dict(kwargs),
            )
            self._next_new_page_id = self._find_next_new_page_id(self._next_new_page_id + 1)
            return obj
        if method == "commit":
            kwargs = self._eval_keywords(node)
            if node.args or kwargs or not statement:
                self._error(node, "sdk.commit() must be an argument-free standalone statement")
            if self._committed:
                self._error(node, "sdk.commit() may appear only once")
            self._committed = True
            return None
        if method == "existing":
            self._error(node, "sdk.existing() is reserved for system-provided bindings")
        self._error(
            node,
            f"unknown SDK method sdk.{method}()",
            allow_tool_retry=method in ("read", "search"),
        )

    def _call_memory(
        self, owner: _MemoryObject, method: str, node: ast.Call, *, statement: bool
    ) -> None:
        if not statement:
            self._error(node, f"{method}() must be a standalone statement")
        if not owner.existing and not owner.name:
            self._error(node, "assign a new memory object before calling methods on it")
        if method == "update":
            if node.args:
                self._error(node, "update() accepts keyword arguments only")
            schema = self.schemas[owner.memory_type]
            allowed_fields = {
                name for name, _type, _description in _protocol_fields(self.context, schema)
            }
            kwargs = self._eval_keywords(
                node,
                allowed=allowed_fields,
                ignore_unknown=True,
                alias_map=self._field_alias_to_real.get(owner.memory_type),
            )
            if _contains_memory_object(kwargs):
                self._error(node, "memory objects cannot be used as business field values")
            if any(isinstance(value, _FieldHandle) for value in kwargs.values()):
                self._error(
                    node,
                    "to edit a string field in place use obj.field.edit(...)/obj.field.drop(...); "
                    "obj.update() only takes complete field values",
                )
            self._update(owner, kwargs, node)
            return None
        if method == "delete":
            kwargs = self._eval_keywords(node)
            if node.args or set(kwargs) - {"replacement"}:
                self._error(node, "delete() accepts only replacement=")
            if not owner.existing:
                self._error(node, "a memory created in this program cannot be deleted")
            if self.schemas[owner.memory_type].operation_mode == "add_only":
                self._error(node, "delete() is unavailable for the selected memory schemas")
            replacement = kwargs.get("replacement")
            if replacement is not None and not isinstance(replacement, _MemoryObject):
                self._error(node, "delete replacement must be a memory object")
            if replacement is not None and not replacement.existing and not replacement.name:
                self._error(node, "assign a new replacement object before referencing it")
            if replacement is not None and replacement.memory_type != owner.memory_type:
                self._error(node, "delete replacement must have the same memory type")
            if replacement is owner:
                self._error(node, "a memory cannot replace itself")
            owner.deleted = True
            owner.replacement = replacement
            return None
        if method == "link":
            kwargs = self._eval_keywords(node)
            if not self.context.link_enabled:
                self._error(node, "links are disabled by configuration")
            if len(node.args) != 1:
                self._error(node, "link() requires exactly one target memory object")
            target = self._eval(node.args[0])
            if not isinstance(target, _MemoryObject):
                self._error(node, "link target must be a memory object")
            if not target.existing and not target.name:
                self._error(node, "assign a new link target before referencing it")
            allowed = {"link_type", "weight", "match_text", "description"}
            if set(kwargs) - allowed:
                self._error(node, "link() received unsupported arguments")
            self.links.append(
                {
                    "f": owner.page_id,
                    "t": target.page_id,
                    "link_type": kwargs.get("link_type", "related_to"),
                    "weight": kwargs.get("weight", 0.5),
                    "match_text": kwargs.get("match_text"),
                    "description": kwargs.get("description", ""),
                }
            )
            return None
        self._error(
            node,
            f"unknown memory method {method}()",
            allow_tool_retry=method in ("read", "search"),
        )

    def _call_field(self, handle: _FieldHandle, method: str, node: ast.Call) -> _FieldHandle:
        if method == "update":
            kwargs = self._eval_keywords(node)
            if kwargs or len(node.args) != 1:
                self._error(
                    node,
                    "field.update() takes exactly one positional argument: the complete new value",
                )
            value = self._eval(node.args[0])
            if _contains_memory_object(value) or isinstance(value, _FieldHandle):
                self._error(node, "field.update() value must be a plain string")
            if handle.blocks or handle.full_value is not _UNSET:
                self._error(node, "field.update() cannot be chained with edit()/drop()")
            handle.full_value = value
            return handle
        if method in ("edit", "drop"):
            if handle.full_value is not _UNSET:
                self._error(node, "field.edit()/drop() cannot be chained after update()")
            kwargs = self._eval_keywords(node)
            if method == "edit":
                if node.args or set(kwargs) != {"search", "replace"}:
                    self._error(
                        node,
                        "field.edit() requires exactly search= and replace= keyword arguments: "
                        "obj.field.edit(search=..., replace=...)",
                    )
                block: Any = SearchReplaceBlock(search=kwargs["search"], replace=kwargs["replace"])
            else:
                if node.args or set(kwargs) != {"text"}:
                    self._error(
                        node, "field.drop() requires exactly text=: obj.field.drop(text=...)"
                    )
                block = DeleteBlock(delete=kwargs["text"])
            if _contains_memory_object(block):
                self._error(node, "memory objects cannot be used as patch text")
            handle.blocks.append(block)
            return handle
        self._error(
            node,
            f"unknown field method {method}(); use obj.field.update(...), "
            "obj.field.edit(search=..., replace=...), or obj.field.drop(text=...)",
        )

    def _apply_field_handle(self, handle: _FieldHandle, node: ast.AST) -> None:
        owner = handle.owner
        name = handle.field_name
        schema = self.schemas[owner.memory_type]
        field_schema = {item.name: item for item in schema.fields}.get(name)
        if handle.full_value is _UNSET and not handle.blocks:
            self._error(node, f"{owner.name}.{name} edit has no update()/edit()/drop() call")
        if (
            field_schema is not None
            and field_schema.merge_op == MergeOp.IMMUTABLE
            and owner.existing
        ):
            # Immutable identity fields cannot change on an existing memory; ignore silently.
            return
        if handle.full_value is not _UNSET:
            owner.fields[name] = handle.full_value
            owner.changed_fields[name] = handle.full_value
            return
        # Local edit chain: only valid for existing PATCH string fields.
        if not owner.existing:
            self._error(
                node,
                "field.edit()/drop() can only patch an existing memory; a memory created in "
                "this program has no prior content — pass the complete value to field.update()",
            )
        if field_schema is None or not (
            field_schema.merge_op == MergeOp.PATCH and field_schema.field_type == FieldType.STRING
        ):
            self._error(node, f"field {name!r} does not support edit()/drop()")
        # Do NOT apply here. Store the edits as a StrPatch so python mode flows through
        # the same resolve_operations -> _validate_patch_operations -> patch-repair path
        # as json mode: a failed snippet is isolated to its own operation and gets the
        # structured patch-repair retry instead of failing the whole program.
        # Accumulate across multiple obj.field.edit()/drop() statements on the same field.
        existing = owner.changed_fields.get(name)
        prior_blocks = existing.blocks if isinstance(existing, StrPatch) else []
        patch = StrPatch(blocks=[*prior_blocks, *handle.blocks])
        owner.fields[name] = patch
        owner.changed_fields[name] = patch

    def _eval_keywords(
        self,
        node: ast.Call,
        *,
        allowed: set[str] | None = None,
        ignore_unknown: bool = False,
        alias_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for item in node.keywords:
            name = item.arg
            if alias_map is not None:
                # Field names use identifier aliases on the DSL surface; map back
                # to the real schema field name before validating/storing.
                name = alias_map.get(name, name)
            if name in values:
                self._error(node, f"keyword argument {name!r} was provided more than once")
            if allowed is not None and name not in allowed and ignore_unknown:
                continue
            values[name] = self._eval(item.value)
        return values

    def _update(self, owner: _MemoryObject, changes: dict[str, Any], node: ast.AST) -> None:
        schema = self.schemas[owner.memory_type]
        fields = {item.name: item for item in schema.fields}
        dynamic_fields = {
            name for name, _type, _description in _protocol_fields(self.context, schema)
        } - fields.keys()
        owner.deleted = False
        owner.replacement = None
        for name, value in changes.items():
            field_schema = fields.get(name)
            if name in dynamic_fields:
                if not owner.existing:
                    owner.fields[name] = value
                    owner.changed_fields[name] = value
                continue
            if field_schema is None:
                continue
            if field_schema.merge_op == MergeOp.IMMUTABLE and owner.existing:
                continue
            current = owner.fields.get(name)
            if field_schema.merge_op == MergeOp.SUM:
                value = (current or 0) + value
                previous_delta = owner.changed_fields.get(name, 0)
                owner.changed_fields[name] = previous_delta + changes[name]
                owner.fields[name] = value
                continue
            owner.fields[name] = value
            owner.changed_fields[name] = value

    def _build_operations(self) -> Any:
        payload: dict[str, Any] = {name: [] for name in self.context.operations_model.model_fields}
        deleted_ids: set[int] = set()
        replacement_ids: dict[int, int] = {}
        for obj in self.objects.values():
            if obj.deleted:
                deleted_ids.add(obj.page_id)
                if obj.replacement is not None:
                    replacement_ids[obj.page_id] = obj.replacement.page_id
                payload.setdefault("delete_ids", []).append(
                    {
                        "delete_page_id": obj.page_id,
                        "replacement_page_id": (
                            obj.replacement.page_id if obj.replacement is not None else None
                        ),
                    }
                )
                continue
            if not obj.changed_fields:
                continue
            fields = dict(obj.changed_fields)
            if obj.existing:
                schema = self.schemas[obj.memory_type]
                for memory_field in schema.fields:
                    if memory_field.merge_op == MergeOp.IMMUTABLE:
                        if memory_field.name in obj.fields:
                            fields[memory_field.name] = obj.fields[memory_field.name]
            payload[obj.memory_type].append({"page_id": obj.page_id, **fields})
        if self.context.link_enabled:
            payload["links"] = []
            for link in self.links:
                remapped = dict(link)
                skip = False
                for endpoint in ("f", "t"):
                    replacement_id = self._resolve_replacement(
                        remapped[endpoint], deleted_ids, replacement_ids
                    )
                    if replacement_id is None:
                        skip = True
                        break
                    remapped[endpoint] = replacement_id
                if not skip and remapped["f"] != remapped["t"]:
                    payload["links"].append(remapped)
        try:
            return self.context.operations_model.model_validate(payload)
        except Exception as exc:
            raise ExtractionOutputProtocolError(
                f"SDK operations failed schema validation: {exc}"
            ) from exc

    @staticmethod
    def _resolve_replacement(
        page_id: int, deleted_ids: set[int], replacement_ids: dict[int, int]
    ) -> int | None:
        visited = set()
        while page_id in deleted_ids:
            if page_id in visited:
                return None
            visited.add(page_id)
            replacement_id = replacement_ids.get(page_id)
            if replacement_id is None:
                return None
            page_id = replacement_id
        return page_id

    def _eval_slice(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Slice):
            return slice(
                self._eval(node.lower) if node.lower else None,
                self._eval(node.upper) if node.upper else None,
                self._eval(node.step) if node.step else None,
            )
        return self._eval(node)

    def _eval_formatted_value(self, node: ast.FormattedValue) -> str:
        value = self._eval(node.value)
        if _contains_memory_object(value):
            self._error(node, "memory objects cannot be formatted")
        if node.conversion == ord("r"):
            value = repr(value)
        elif node.conversion == ord("a"):
            value = ascii(value)
        elif node.conversion == ord("s"):
            value = str(value)
        elif node.conversion != -1:
            self._error(node, "unsupported f-string conversion")
        format_spec = self._eval(node.format_spec) if node.format_spec is not None else ""
        return format(value, format_spec)

    def _eval_binop(self, node: ast.BinOp) -> Any:
        if isinstance(node.op, ast.Mult):
            return self._eval_mult(node)
        left, right = self._eval(node.left), self._eval(node.right)
        operations = {
            ast.Add: lambda: self._checked_concat(node, left, right),
            ast.Sub: lambda: left - right,
            ast.Div: lambda: left / right,
            ast.FloorDiv: lambda: left // right,
            ast.Mod: lambda: left % right,
        }
        operation = operations.get(type(node.op))
        if operation is None:
            self._error(node, "unsupported binary operator")
        return operation()

    def _sequence_repeat_size(self, sequence: Any, count: Any, node: ast.AST) -> int:
        if not isinstance(count, int) or isinstance(count, bool):
            self._error(node, "a repeated literal must be multiplied by an integer count")
        if count < 0:
            self._error(node, "a repeated literal count cannot be negative")
        if isinstance(sequence, str):
            unit = len(sequence.encode("utf-8"))
        elif isinstance(sequence, (bytes, bytearray)):
            unit = len(sequence)
        else:
            unit = len(sequence)
        return unit * count

    def _checked_repeat(self, node: ast.AST, sequence: Any, count: Any) -> Any:
        if not isinstance(sequence, _REPEATABLE_SEQUENCES):
            self._error(node, "multiplication is only supported for repeating a literal")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            self._error(node, "a repeated literal must be multiplied by a non-negative integer")
        if self._sequence_repeat_size(sequence, count, node) > _MAX_EXPRESSION_SIZE:
            self._error(
                node,
                f"repeated literal would exceed the {_MAX_EXPRESSION_SIZE:,}-character size limit",
            )
        return sequence * count

    def _eval_mult(self, node: ast.BinOp) -> Any:
        left_node, right_node = node.left, node.right
        # Repeat a literal sequence by an integer count. Both a bare literal
        # and a literal nested inside another repeatable expression
        # (e.g. ["..."] * n inside "".join(...)) are accepted; evaluate only
        # after the would-be result size is bounded, before any allocation.
        left_seq, left_count = self._repeat_operands(left_node, right_node)
        right_seq, right_count = self._repeat_operands(right_node, left_node)
        if left_seq is not None:
            return self._checked_repeat(node, left_seq, left_count)
        if right_seq is not None:
            return self._checked_repeat(node, right_seq, right_count)
        self._error(
            node,
            "multiplication is only supported to repeat a string/list literal "
            "by a non-negative integer count",
        )

    def _repeat_operands(self, seq_node: ast.AST, count_node: ast.AST) -> tuple[Any, Any]:
        if isinstance(seq_node, ast.Constant) and isinstance(seq_node.value, _REPEATABLE_SEQUENCES):
            return seq_node.value, self._eval(count_node)
        # A 1-element list literal (ast.List with one Constant element) is a
        # common way to build a repeatable argument for str.join.
        if (
            isinstance(seq_node, ast.List)
            and len(seq_node.elts) == 1
            and isinstance(seq_node.elts[0], ast.Constant)
        ):
            return [seq_node.elts[0].value], self._eval(count_node)
        return None, None

    def _checked_concat(self, node: ast.AST, left: Any, right: Any) -> Any:
        if isinstance(left, str) and isinstance(right, str):
            if len(left.encode("utf-8")) + len(right.encode("utf-8")) > _MAX_EXPRESSION_SIZE:
                self._error(
                    node,
                    f"concatenated string exceeds the {_MAX_EXPRESSION_SIZE:,}-character size limit",
                )
        elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            if len(left) + len(right) > _MAX_EXPRESSION_SIZE:
                self._error(
                    node, f"concatenated list exceeds the {_MAX_EXPRESSION_SIZE:,}-item size limit"
                )
        return left + right

    def _eval_compare(self, node: ast.Compare) -> bool:
        left = self._eval(node.left)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            right = self._eval(comparator)
            if isinstance(operator, ast.Eq):
                matches = left == right
            elif isinstance(operator, ast.NotEq):
                matches = left != right
            elif isinstance(operator, ast.Lt):
                matches = left < right
            elif isinstance(operator, ast.LtE):
                matches = left <= right
            elif isinstance(operator, ast.Gt):
                matches = left > right
            elif isinstance(operator, ast.GtE):
                matches = left >= right
            elif isinstance(operator, ast.In):
                matches = left in right
            elif isinstance(operator, ast.NotIn):
                matches = left not in right
            elif isinstance(operator, ast.Is):
                matches = left is right
            elif isinstance(operator, ast.IsNot):
                matches = left is not right
            else:
                self._error(node, "unsupported comparison operator")
            if not matches:
                return False
            left = right
        return True

    def _find_next_new_page_id(self, candidate: int) -> int:
        while self.context.page_id_map.resolve(candidate) is not None:
            candidate += 1
        return candidate

    @staticmethod
    def _error(node: ast.AST, message: str, *, allow_tool_retry: bool = False) -> None:
        raise ExtractionOutputProtocolError(
            f"Line {getattr(node, 'lineno', 1)}: {message}",
            allow_tool_retry=allow_tool_retry,
        )


def _extract_python_code(content: str) -> str:
    stripped = str(content or "").strip()
    matches = list(_PYTHON_FENCE_RE.finditer(stripped))
    if len(matches) == 1:
        match = matches[0]
        surrounding_text = stripped[: match.start()] + stripped[match.end() :]
        if "```" not in surrounding_text:
            return match.group("code").rstrip()
    starts = list(_PYTHON_FENCE_START_RE.finditer(stripped))
    if len(starts) == 1 and stripped.count("```") == 1:
        return stripped[starts[0].end() :].strip()
    if "```" in stripped:
        raise ExtractionOutputProtocolError(
            "Python output may contain only one complete ```python code fence"
        )
    return stripped


def _is_string_literal_syntax_error(error: str) -> bool:
    """Heuristic: does this parse error point at a broken string literal?

    These are almost always caused by an apostrophe or quote inside prose that
    was wrapped in a single-/double-quoted literal instead of a triple-quoted
    one, so the retry should push hard on triple quotes.
    """
    lowered = error.lower()
    return any(
        marker in lowered
        for marker in (
            "unterminated string literal",
            "unterminated triple-quoted string",
            "invalid syntax. perhaps you forgot a comma",
            "eol while scanning string literal",
            "invalid escape sequence",
        )
    )


def _format_syntax_error_source(exc: SyntaxError, *, max_len: int = 120) -> str:
    """Render the offending source line (and a caret) from a SyntaxError.

    Python's own message (e.g. "Perhaps you forgot a comma?") is often
    misleading for these programs; the real cause is usually a quote or newline
    inside prose that should have been triple-quoted. Showing the actual line
    lets the model see the break point instead of guessing.
    """
    text = (exc.text or "").rstrip("\n")
    if not text:
        return ""
    stripped = text.lstrip()
    lead = len(text) - len(stripped)
    offset = exc.offset
    caret_col = None
    if isinstance(offset, int) and offset >= 1:
        caret_col = max(0, offset - 1 - lead)
    if len(stripped) > max_len:
        stripped = stripped[:max_len] + "…"
        if caret_col is not None and caret_col > max_len:
            caret_col = None
    lines = [f"\n  {stripped}"]
    if caret_col is not None and caret_col <= len(stripped):
        lines.append("\n  " + " " * caret_col + "^")
    return "".join(lines)


def _looks_like_bare_markdown(code: str) -> bool:
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    return (
        bool(lines)
        and lines[0].startswith("# ")
        and any(line.startswith(("- ", "* ")) for line in lines[1:])
        and not any("sdk." in line for line in lines)
    )


def _visible_memory_fields(
    memory_file: MemoryFile,
    schema: MemoryTypeSchema,
    context: ExtractionOutputContext,
) -> dict[str, Any]:
    schema_fields = {name for name, _type, _description in _protocol_fields(context, schema)}
    fields = {
        key: value
        for key, value in dict(memory_file.extra_fields or {}).items()
        if key in schema_fields and key not in _HIDDEN_MEMORY_FIELDS and not key.startswith("_")
    }
    if "content" in schema_fields:
        fields["content"] = memory_file.plain_content()
    return fields


def _field_type_name(field_type: FieldType) -> str:
    return {
        FieldType.STRING: "str",
        FieldType.INT64: "int",
        FieldType.FLOAT32: "float",
        FieldType.BOOL: "bool",
    }.get(field_type, "object")


def _protocol_fields(
    context: ExtractionOutputContext, schema: MemoryTypeSchema
) -> list[tuple[str, str, str]]:
    static_fields = [
        (field.name, _field_type_name(field.field_type), field.description)
        for field in schema.fields
    ]
    static_names = {name for name, _type, _description in static_fields}
    operations_field = context.operations_model.model_fields.get(schema.memory_type)
    annotation = getattr(operations_field, "annotation", None)
    model_type = get_args(annotation)[0] if get_origin(annotation) is list else annotation
    dynamic_fields = getattr(model_type, "model_fields", {})
    extras = []
    for name, model_field in dynamic_fields.items():
        if name == "page_id" or name in static_names:
            continue
        # Non-identifier field names are aliased on the DSL surface (see
        # _identifier_alias), so they need not be valid Python identifiers here.
        extras.append(
            (name, _annotation_type_name(model_field.annotation), model_field.description or "")
        )
    return [*extras, *static_fields]


def _model_visible_identity_fields(
    context: ExtractionOutputContext, schema: MemoryTypeSchema
) -> tuple[str, ...]:
    visible_fields = {name for name, _type, _description in _protocol_fields(context, schema)}
    return tuple(name for name in schema.identity_fields() if name in visible_fields)


def _memory_identity(
    schema: MemoryTypeSchema, fields: dict[str, Any]
) -> tuple[tuple[str, Any], ...]:
    return tuple(
        (
            name,
            _normalized_peer_scope(fields.get(name)) if name == "peer_id" else fields.get(name),
        )
        for name in schema.identity_fields()
    )


def _format_identity(identity: tuple[tuple[str, Any], ...]) -> str:
    return ", ".join(
        f"{name}={'<self>' if name == 'peer_id' and value is None else value!r}"
        for name, value in identity
    )


def _annotation_type_name(annotation: Any) -> str:
    args = [item for item in get_args(annotation) if item is not type(None)]
    if args:
        annotation = args[0]
    return getattr(annotation, "__name__", "object")


def _normalized_peer_scope(value: Any) -> str | None:
    if value in (None, "", "__self"):
        return None
    return safe_peer_id(value)


def _contains_memory_object(value: Any) -> bool:
    if isinstance(value, _MemoryObject):
        return True
    if isinstance(value, dict):
        return any(
            _contains_memory_object(key) or _contains_memory_object(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_memory_object(item) for item in value)
    return False


def _require_identifier(value: str, label: str) -> None:
    if not value.isidentifier() or keyword.iskeyword(value):
        raise ValueError(
            f"Python memory output requires {label} {value!r} to be a valid identifier"
        )


def _identifier_alias(name: str) -> str:
    """Map a schema name to a Python-identifier alias used only on the DSL surface.

    memory_type and field names keep their real value everywhere (URI, storage,
    JSON protocol); only the Python method/parameter names need to be valid
    identifiers, so non-identifier characters are folded to underscores.
    """
    alias = re.sub(r"\W", "_", name)
    if alias and alias[0].isdigit():
        alias = f"_{alias}"
    if not alias:
        alias = "_"
    if keyword.iskeyword(alias):
        alias = f"{alias}_"
    return alias
