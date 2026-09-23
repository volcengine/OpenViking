"""Typed, collection-level Compile plans. Python syntax is parsed, never executed."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

PROCESSING_VERSION = "compile-pipeline-31"
# Explicit output-token fallback when the configured VLM provides no value.
DEFAULT_MAX_TOKENS = 32_000

# Common tasks share one collection flow; explicit plans still pass the AST whitelist.
DEFAULT_PLAN = (
    "records = p.map(sources, task=contract.extract)\n"
    "groups = p.shuffle(records, by=contract.routing, against=target)\n"
    "changes = p.reduce(groups, task=contract.reduce)\n"
    "p.finalize(changes, into=target)"
)


def digest(value: Any) -> str:
    """Hash JSON-compatible task inputs deterministically, including their versions."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def content_hash(value: str | bytes) -> str:
    """Return the byte hash used by conditional content writes."""
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transform(StrictModel):
    """A bounded transformation; fields declare the permitted intermediate structure.

    Hierarchical aggregation is allowed only through an explicitly supplied combine
    transform. Its fields must carry the Skill's required conditions and exceptions.
    """

    instructions: str = Field(min_length=1, max_length=6000)
    output: Literal["records", "files"] = "records"
    execution: Literal["direct", "agent"] = "direct"
    input_unit: Literal["range", "file"] = Field(
        default="range",
        description="Source Map assignment boundary. Use range for partial evidence "
        "that later stages synthesize; use file when the Map result requires "
        "whole-document context. State that dependency in instructions. "
        "Intermediate records stay separate.",
    )
    fields: dict[str, str] = Field(
        default_factory=lambda: {"text": "Facts extracted from the source."},
        description="Payload field names mapped to optional simple descriptions; empty descriptions "
        "are allowed. Prefer one sentence per description, "
        "without a count limit. Runtime keys inputs, scope, "
        "routing_text, evidence_spans, ready_path, ready_content, ready_content_ref and target_uri are already "
        "provided beside payload and must not be repeated. Leave the default for files output.",
    )

    @field_validator("fields", mode="before")
    @classmethod
    def field_descriptions(cls, value):
        """Preserve field order, treating name-only lists and null descriptions as undescribed."""
        if isinstance(value, list) and all(isinstance(name, str) for name in value):
            return dict.fromkeys(value, "")
        if isinstance(value, dict):
            return {
                name: "" if description is None else description
                for name, description in value.items()
            }
        return value

    @field_validator("fields")
    @classmethod
    def check_fields(cls, value):
        """Preserve business field descriptions and order while omitting reserved names.

        An empty mapping is valid when no business fields remain after normalization.
        """
        reserved = RecordDraft.model_fields.keys() - {"payload"}
        return {name: description for name, description in value.items() if name not in reserved}


class Routing(StrictModel):
    """Group records by semantic instructions or preserve the entire collection as one group."""

    mode: Literal["semantic", "all"] = "semantic"
    instructions: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_instructions(self) -> Routing:
        """Semantic grouping needs criteria; global grouping needs no model decision."""
        if self.mode == "semantic" and not self.instructions.strip():
            raise ValueError("Semantic routing requires instructions")
        return self


class Contract(StrictModel):
    """Task-local interpretation of the original Skill, which remains authoritative.

    distinguish maps scope field names to their extraction meanings.
    Scope values describe evidence; they are not equality keys for candidate grouping.
    unsupported requirements stop planning instead of silently weakening the Skill.
    No identifiers in this contract enumerate individual input documents.
    """

    version: Literal[1] = 1
    extract: Transform
    reduce: Transform | None = None
    synthesize: Transform | None = None
    combine: Transform | None = None
    routing: Annotated[str, Field(max_length=4000)] | Routing = ""
    final_routing: Annotated[str, Field(max_length=4000)] | Routing = ""
    distinguish: dict[str, str] = Field(
        default_factory=dict,
        description="Scope field names mapped to their meanings. Prefer short names such as "
        "subject, page_role and version; explain what to extract in each value.",
    )
    preserve: list[str] = Field(default_factory=list, max_length=16)
    overflow: Literal["direct", "structured"] = "direct"
    output_format: Literal["wiki", "files"] = Field(
        default="files",
        description="wiki requires OKF Markdown pages; files permits arbitrary/mixed text files.",
    )
    required_paths: list[str] = Field(default_factory=list, max_length=16)
    validation: str = Field(default="", max_length=4000)
    unsupported: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Only missing runtime capabilities that prevent this task. Deferred validation, "
        "checks delegated to operators are supported.",
    )

    @field_validator("distinguish", mode="before")
    @classmethod
    def scope_descriptions(cls, value):
        """Read saved name lists as fields without descriptions; mappings retain their meanings."""
        return dict.fromkeys(value, "") if isinstance(value, list) else value

    @model_validator(mode="before")
    @classmethod
    def apply_transform_defaults(cls, value):
        """Default Reduce output to files while preserving explicit output choices."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if isinstance(value.get("reduce"), dict):
            value["reduce"] = {"output": "files", **value["reduce"]}
        return value

    @model_validator(mode="after")
    def check_contract(self) -> Contract:
        """Reject unusable contracts before any source transformation begins."""
        if self.unsupported:
            raise ValueError(f"Unrepresentable Skill requirements: {self.unsupported}")
        if self.overflow == "structured" and (
            self.combine is None or self.combine.output != "records"
        ):
            raise ValueError("structured overflow requires a records combine transform")
        return self


class PlanProposal(StrictModel):
    """A task contract with an optional custom flow; omission uses the four standard operators."""

    contract: Contract
    plan: str = Field(default=DEFAULT_PLAN, min_length=1, max_length=8000)


@dataclass(frozen=True)
class Node:
    """A validated operation over an already-bound dataset; order is topological."""

    name: str
    op: str
    source: str
    task: str = ""
    against_target: bool = False


def parse_plan(program: str, contract: Contract) -> list[Node]:
    """Compile a small AST whitelist into typed nodes; no Python objects are evaluated.

    Plan text is limited to 8,000 characters before parsing. Plans have a single terminal
    finalize, no unused datasets, rebinding, implicit fan-out or literals.
    Each Shuffle record belongs to one work set. Invalid plans raise ValueError.
    """
    if len(program) > 8000:
        raise ValueError("Plan exceeds 8000 characters")
    try:
        tree = ast.parse(program)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid plan syntax") from exc
    if not tree.body:
        raise ValueError("Plan must contain at least one operation")
    handles = {"sources": "sources"}
    used: set[str] = set()
    nodes = []

    def reference(value: ast.AST, owner: str, allowed: set[str], field: str) -> str:
        """Resolve a direct reference or report its source line, field and allowed expressions."""
        if not (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == owner
            and value.attr in allowed
        ):
            expected = ", ".join(f"{owner}.{name}" for name in sorted(allowed))
            raise ValueError(
                f"plan line {value.lineno}, {field}: received {ast.unparse(value)}. "
                f"Expected one of: {expected}."
            )
        return value.attr

    for index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            lhs = statement.targets[0]
            if not isinstance(lhs, ast.Name) or lhs.id.startswith("_"):
                raise ValueError("Dataset names must be plain identifiers")
            name = lhs.id
            call = statement.value
        elif isinstance(statement, ast.Expr):
            name, call = "result", statement.value
        else:
            raise ValueError("Only dataset assignments and a final p.finalize are allowed")
        if name in handles or name in {"p", "contract", "target", "sources"}:
            raise ValueError(f"Rebinding forbidden: {name}")
        if not isinstance(call, ast.Call):
            raise ValueError("Expected pipeline call")
        op = reference(call.func, "p", {"map", "shuffle", "reduce", "finalize"}, "operator")
        if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
            raise ValueError("An operator takes one dataset handle")
        source = call.args[0].id
        if source not in handles or source in used:
            raise ValueError(f"Unbound or multiply consumed dataset: {source}")
        kwargs = {k.arg: k.value for k in call.keywords}
        if len(kwargs) != len(call.keywords) or None in kwargs:
            raise ValueError("Duplicate or expanded keywords forbidden")
        task, against = "", False
        if op in {"map", "reduce"}:
            required = {"task"}
            if set(kwargs) != required:
                hint = (
                    " Configure overflow at contract.overflow; remove the overflow keyword "
                    "from this call."
                    if "overflow" in kwargs
                    else ""
                )
                raise ValueError(
                    f"plan line {call.lineno}, node {name}: p.{op} accepts only the task keyword; "
                    f"missing={sorted(required - set(kwargs))}; unexpected={sorted(set(kwargs) - required)}. "
                    f"Received {ast.unparse(call)}.{hint}"
                )
            task = reference(
                kwargs["task"], "contract", {"extract", "reduce", "synthesize"}, "task"
            )
            transform = getattr(contract, task)
            if transform is None:
                raise ValueError(f"Missing transform: {task}")
            if op == "reduce":
                valid = handles[source] == "groups"
            else:
                valid = handles[source] in {"sources", "records"}
            output_type = transform.output
        elif op == "shuffle":
            if set(kwargs) not in ({"by"}, {"by", "against"}):
                raise ValueError("shuffle requires by and optional against=target")
            task = reference(kwargs["by"], "contract", {"routing", "final_routing"}, "by")
            if not getattr(contract, task):
                raise ValueError(f"Missing routing requirements: {task}")
            if "against" in kwargs:
                value = kwargs["against"]
                if not isinstance(value, ast.Name) or value.id != "target":
                    raise ValueError("Only the bound target can be searched")
                against = True
            valid, output_type = handles[source] == "records", "groups"
        else:
            into = kwargs.get("into")
            if set(kwargs) != {"into"} or not isinstance(into, ast.Name) or into.id != "target":
                raise ValueError("finalize requires into=target")
            valid, output_type = handles[source] == "files", "result"
            if index != len(tree.body) - 1:
                raise ValueError("finalize must be the final operation")
        if not valid or (isinstance(statement, ast.Expr) and op != "finalize"):
            raise ValueError(f"Invalid dataset type for {op}: {handles[source]}")
        used.add(source)
        handles[name] = output_type
        nodes.append(Node(name, op, source, task, against))
    if nodes[-1].op != "finalize" or set(handles) - used != {nodes[-1].name}:
        raise ValueError("Every dataset must reach finalize")
    return nodes


class EvidenceSpan(StrictModel):
    """Original evidence location; line numbers are one-based, inclusive and shard-local."""

    source_range: str
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)


class RecordDraft(StrictModel):
    """Model payload with local input references; runtime assigns identity and evidence."""

    inputs: list[str] = Field(min_length=1)
    # Optional reading hints; omitted ranges retain full-shard evidence access.
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    # JSON preserves nested facts and relations without prescribing their business shape.
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    routing_text: str = Field(min_length=1, max_length=600)
    scope: dict[str, str] = Field(default_factory=dict)
    # A path without content is only a hint, never a finished or publishable file.
    ready_content: str | None = None
    ready_path: str | None = None
    ready_content_ref: str | None = Field(
        default=None, description="Agent scratch file alternative to inline ready_content."
    )
    # An explicit URI present in supplied evidence, used as a candidate before search.
    target_uri: str | None = None

    @model_validator(mode="before")
    @classmethod
    def move_business_fields(cls, value):
        """Move extra record fields into payload without mutating the supplied record.

        Equal duplicates collapse; conflicting values require model repair. Invalid
        payload types and missing structural fields remain subject to normal validation.
        """
        if not isinstance(value, dict) or not isinstance(value.get("payload", {}), dict):
            return value
        extra = value.keys() - cls.model_fields.keys()
        if not extra:
            return value
        record, payload = dict(value), dict(value.get("payload", {}))
        for name in extra:
            if name in payload and payload[name] != record[name]:
                raise ValueError(f"Conflicting values for payload field: {name}")
            payload[name] = record.pop(name)
        return {**record, "payload": payload}

    @field_validator("payload", mode="before")
    @classmethod
    def strip_reserved_fields(cls, value):
        """Drop top-level payload keys owned by the record without changing outer values.

        Nested business data is preserved; invalid payload types still fail validation.
        """
        if isinstance(value, dict):
            reserved = cls.model_fields.keys() - {"payload"}
            return {key: item for key, item in value.items() if key not in reserved}
        return value


class InputReferenceError(ValueError):
    """Invalid input accounting; file agents receive a bounded metadata repair window."""


class MissingReadyPathError(ValueError):
    """Finished content lacks its output path; direct calls allow one extra repair."""


class RecordResponse(StrictModel):
    records: list[RecordDraft] = Field(default_factory=list, max_length=64)


def result_schema(schema, data):
    """Expose assignment fields and routing identities in direct and child tool schemas."""
    # The model receives strict routing instructions; malformed individual decisions
    # remain available to Shuffle so valid neighbours survive a partial response.
    result = (RouteResponse if schema is RouteBatchResponse else schema).model_json_schema()
    if schema is PlanProposal:
        output = result["$defs"]["Transform"]["properties"]["output"]
        output.pop("default")
        output["description"] = "Defaults to records; contract.reduce defaults to files."
    if schema in (RouteResponse, RouteBatchResponse):
        ids = [item["record"] for item in data["records"]]
        result["properties"]["decisions"].update(minItems=len(ids), maxItems=len(ids))
        result["$defs"]["RouteDecision"]["properties"]["record"]["enum"] = ids
    if schema is FileResponse and data.get("inputs"):
        # Limit file lineage to supplied inputs without requiring exhaustive coverage.
        ids = [item["id"] for item in data["inputs"]]
        inputs = result["$defs"]["FileDraft"]["properties"]["inputs"]
        inputs["items"]["enum"] = ids
        inputs["uniqueItems"] = True
    if schema is FileResponse and "indexes" in data:
        paths = [item["group"]["path"] for item in data["indexes"]]
        result["properties"]["files"].update(minItems=len(paths), maxItems=len(paths))
        result["$defs"]["FileDraft"]["properties"]["path"]["enum"] = paths
    if schema is RecordResponse and "record_fields" in data:
        record = result["$defs"]["RecordDraft"]
        properties = record["properties"]
        record["required"] = ["inputs", "routing_text", "scope"]
        for name, fields in (("payload", data["record_fields"]), ("scope", data["scope_fields"])):
            value = properties[name]["additionalProperties"]
            if name == "scope":
                value = {"type": "string"}
            properties[name] = {
                "type": "object",
                "properties": {
                    field: {**value, "description": fields[field]}
                    if isinstance(fields, dict)
                    else dict(value)
                    for field in fields
                },
                "additionalProperties": value,
            }
    return result


class RouteDecision(StrictModel):
    """Candidate links for joint processing, without asserting identity or output paths.

    related names only this primary record's candidates for joint consideration.
    history names only this primary record's recalled URIs, not mandatory updates.
    Empty lists retain the record as an independent work set.
    """

    record: str
    related: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)


class RouteResponse(StrictModel):
    decisions: list[RouteDecision] = Field(
        description="Exactly one decision per supplied record ID, with no duplicates. "
        "Links request joint processing; Reduce decides the number and paths of output files.",
    )


class RouteBatchResponse(StrictModel):
    """Unvalidated routing entries; Shuffle validates and retries each primary independently.

    Only the envelope is parsed here. Individual entries may be malformed and must
    never enter the accepted-route store or the model's validated-response cache.
    """

    decisions: list[Any]


class Patch(StrictModel):
    """Exact, unique old-text anchor and its replacement, bound to an old-file hash."""

    old: str = Field(
        min_length=1,
        description="Exact text occurring once in the supplied old content; anchors must not overlap.",
    )
    new: str = Field(description="Replacement text for this anchor; empty text deletes it.")


class FileDraft(StrictModel):
    """An inline edit or a child-local file reference resolved before semantic validation.

    content_ref is relative to the submitting child's scratch directory. Runtime
    records and verifies content_sha256; callers may supply it to assert a version.
    """

    path: str = Field(description="Destination path relative to the compile target.")
    content: str | None = Field(
        default=None,
        description="Complete file content. Omit when using content_ref or patches.",
    )
    content_ref: str | None = Field(
        default=None,
        description="Existing file to read, relative to your scratch root. "
        "Use this after write_file; omit inline content.",
    )
    content_sha256: str | None = None
    patches: list[Patch] = Field(
        default_factory=list,
        description="Edits to supplied historical content; do not combine with content or content_ref.",
    )
    base_hash: str | None = Field(
        default=None,
        description="Copy the supplied historical file hash when updating; omit for new files.",
    )
    inputs: list[str] = Field(
        min_length=1,
        description="Unique supplied input IDs actually used by this file; multiple inputs may support "
        "one file, and one input may support multiple files.",
    )


class FileResponse(StrictModel):
    """Final files with explicit lineage; unreferenced inputs have unconfirmed coverage."""

    files: list[FileDraft] = Field(default_factory=list)


@dataclass
class Record:
    """A dataset item carrying small routing metadata and references to exact evidence.

    source_refs are runtime-assigned source-range IDs, deduplicated across levels.
    payload_ref addresses one task shard; neither payloads nor vectors enter plans.
    parents records immediate lineage for final-state accounting.
    """

    record_id: str
    payload_ref: str
    source_refs: list[str]
    routing_text: str
    scope: dict[str, str]
    parents: list[str]
    ready_ref: str | None = None
    schema: str = "source-range"
    target_uri: str | None = None


@dataclass
class Group:
    """Evidence to process together; one work set may produce several independent files."""

    group_id: str
    records: list[Record]
    # Authorized recalled files available for comparison and version-checked updates.
    target_uris: list[str] = field(default_factory=list)
